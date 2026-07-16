# MariaDB StatefulSet 트러블슈팅 (2026-07-15)

`mariadb` StatefulSet(2 replica, primary/replica 구성) 배포 중 연쇄적으로 발생한 문제와 조치 기록.

## 1. PVC가 바인딩되지 않음 (`no persistent volumes available for this claim and no storage class is set`)

**증상**
```
Warning  FailedBinding  persistentvolume-controller  no persistent volumes available for this claim and no storage class is set
```

**원인**
`mariadb-statefulset.yaml`의 `volumeClaimTemplates`는 `storageClassName: ""`로 정적 프로비저닝을 쓰도록 설계되어 있고, `mariadb-pv.yaml`의 PV(`mariadb-pv-0`, `mariadb-pv-1`)와 `claimRef`로 1:1 사전 바인딩되게 되어 있음. 그런데 `mariadb-pv-0`가 예전 배포에서 사용되던 PVC가 삭제되며 `persistentVolumeReclaimPolicy: Retain` 정책 때문에 `Released` 상태로 남아 있었음. `Released` 상태의 PV는 `claimRef`가 이전 클레임을 계속 가리키고 있어 새 PVC(`mariadb-data-mariadb-0`)와 자동으로 재바인딩되지 않음.

**조치**
```bash
kubectl patch pv mariadb-pv-0 --type merge -p '{"spec":{"claimRef": null}}'
```
`claimRef`를 제거해 PV를 `Available` 상태로 되돌림. `Retain` 정책이라 `/data/mariadb`의 실제 데이터는 보존됨.

---

## 2. init 컨테이너가 `server-id`를 잘못된 섹션에 삽입 → `postStart` 훅이 영원히 대기 (`PodInitializing` 10분+ 지속)

**증상**
- `mariadb-0` 파드가 `PodInitializing`에서 멈춰 진행되지 않음.
- `kubectl logs`는 "container is waiting to start: PodInitializing"만 반환 (postStart 훅이 끝나지 않으면 kubelet이 컨테이너를 Running으로 승격하지 않기 때문).
- 컨테이너 내부에서 직접 확인한 결과:
  ```
  mariadb-admin: unknown variable 'server-id=1'
  ```

**원인**
init 컨테이너 스크립트가 `primary.cnf`/`replica.cnf`를 복사한 뒤 `server-id`를 파일 **맨 끝에 그냥 append**:
```bash
echo "server-id=$((ordinal + 1))" >> /mnt/conf.d/custom.cnf
```
configmap의 cnf 파일은 `[mysqld]` 섹션 다음에 `[client]` 섹션으로 끝나므로, append된 `server-id`가 `[client]` 섹션 소속이 되어버림. `mariadb-admin`/`mariadb` 같은 클라이언트 도구도 `[client]` 섹션을 읽는데 `server-id`는 서버 전용 변수라 클라이언트 파싱 단계에서 에러가 남. `postStart`의 `until mariadb-admin ping ...` 루프가 이 에러 때문에 절대 성공하지 못하고 무한 재시도.

**조치**
`mariadb-statefulset.yaml`의 init 컨테이너 스크립트를 파일 끝 append 대신 `[mysqld]` 섹션 안에 삽입하도록 수정:
```bash
sed -i "/^\[mysqld\]/a server-id=$((ordinal + 1))" /mnt/conf.d/custom.cnf
```

---

## 3. 호스트에 남은 고아 mariadbd 프로세스가 데이터 파일 잠금을 계속 보유 → `CrashLoopBackOff`

**증상**
```
[ERROR] mariadbd: Can't lock aria control file '/var/lib/mysql/aria_log_control' for exclusive use, error: 11
[ERROR] InnoDB: Unable to lock ./ibdata1 error: 11
```
`kubectl delete pod mariadb-0`로 파드를 재생성해도 동일한 에러가 계속 재발.

**원인**
`hostPath` 기반 PV(`/data/mariadb`)를 쓰는 구성에서, 최초 `mariadb-0` 파드의 `mariadbd` 프로세스가 컨테이너 종료 시 정상적으로 정리되지 않고 **노드에 고아 프로세스로 잔존**. 새로 뜬 컨테이너의 `mariadbd`가 같은 디스크 경로의 `ibdata1`, `aria_log_control` 파일 잠금을 획득하려다 기존 프로세스와 충돌.

쿠버네티스가 관리하는 파드 목록(`kubectl get pods -A -o wide`)에는 잡히지 않으므로, 노드에 직접 접속해 프로세스를 확인해야 했음:
```bash
sudo fuser -v /data/mariadb/ibdata1
# → PID 3190986 mariadbd (elapsed 시간이 최초 파드 기동 시각과 일치)
```

**조치**
worker 노드에서 직접 프로세스 강제 종료:
```bash
sudo kill -9 3190986
```
이후 `kubectl delete pod`로 재기동 시 정상적으로 잠금 획득 및 기동 성공.

---

## 4. `super-read-only` 옵션이 MariaDB 12.3에서 제거됨 → replica 컨테이너가 기동 즉시 실패

**증상**
```
[ERROR] mariadbd: unknown option '--super-read-only'
```
`mariadb-1`(replica)만 크래시, `mariadb-0`(primary)는 정상.

**원인**
`mariadb-configmap.yaml`의 `replica.cnf`에 있던 `super-read-only`가 이번에 사용 중인 MariaDB 12.3.2 이미지에서는 지원되지 않는 옵션. 해당 버전은 `super_read_only`를 별도 변수로 두지 않고 `read_only`가 값을 받는 방식으로 통합:
- `read-only=ON` : ADMIN 권한 없는 사용자만 쓰기 제한 (기존 `read_only`와 동일)
- `read-only=NO_LOCK_NO_ADMIN` : ADMIN 권한이 있어도 쓰기 제한 (기존 `super_read_only`와 동일한 의도)

**1차 조치 (실패)**
`replica.cnf`에 `read-only=NO_LOCK_NO_ADMIN`을 정적으로 넣었더니 새로운 문제 발생:
```
ERROR: 1290  The MariaDB server is running with the --read-only=NO_LOCK_NO_ADMIN option so it cannot execute this statement
Installation of system tables failed!
```
`read_only`를 시작 옵션으로 못박으면, MariaDB 엔트리포인트가 **최초 부팅 시 시스템 테이블을 생성하는 부트스트랩 과정 자체(쓰기 작업)** 가 막혀버리는 닭과 달걀 문제 발생.

**최종 조치**
- `mariadb-configmap.yaml`에서 `read-only=...` 설정을 제거 (정적 cnf에 넣지 않음).
- `mariadb-statefulset.yaml`의 `postStart` 훅, replica 분기(`ordinal != 0`)에서 서버가 완전히 기동된 뒤 동적으로 설정:
  ```bash
  mariadb -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SET GLOBAL read_only='NO_LOCK_NO_ADMIN';"
  ```
  `postStart`는 컨테이너가 (재)시작될 때마다 실행되므로, 매 기동 시 재적용되어 값이 유지됨.

---

## 참고: StatefulSet의 자동 RollingUpdate

manifest 수정 후 `kubectl apply`만 해도, 기본 `updateStrategy: RollingUpdate` 때문에 StatefulSet 컨트롤러가 ordinal 역순(`mariadb-1` → `mariadb-0`)으로 파드를 **자동 재생성**함. 트러블슈팅 중 수동으로 `kubectl delete pod`를 병행하면서 재생성이 중복 발생했음 — manifest만 바꾼 경우 별도로 파드를 지울 필요 없이 apply 후 롤링 업데이트가 끝나길 기다리면 됨.

## 최종 확인

```bash
kubectl get pods -n database
# mariadb-0   1/1   Running
# mariadb-1   1/1   Running

kubectl exec -n database mariadb-1 -c mariadb -- \
  bash -c 'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW SLAVE STATUS\G"'
# Slave_IO_Running: Yes
# Slave_SQL_Running: Yes
# Seconds_Behind_Master: 0
# Last_Error: (없음)
```

## 변경된 파일
- `DB-manifest/mariadb-statefulset.yaml` — init 컨테이너 `server-id` 삽입 방식 수정, `postStart`에 replica `read_only` 동적 설정 추가
- `DB-manifest/mariadb-configmap.yaml` — `replica.cnf`에서 `super-read-only` 제거
