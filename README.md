> **Public replication package for the loop-engineering study.**

# loop-engineering-lab

[[loop-engineering-paper]] 의 측정 하니스. 평가자의 정보 채널 타입(in-band 트랜스크립트 대 out-of-band world-state 오라클)이 자율 루프의 자기기만(progress mirage)과 실세계 산출에 미치는 인과효과를 측정한다. 설계 근거는 paper 레포의 lab-design.md 와 RESEARCH-CORE.md.

공개 데이터와 재현 범위는 [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md),
SE 특별호용 개정 설계와 동결 조건은 [PREREGISTRATION.md](PREREGISTRATION.md)에 있다.

## 2026-08 SE 확장

- `analysis/aidev_pilot.py`: AIDev 10,000-PR 탐색적 타당성 분석. 원문이나
  식별자를 내보내지 않고 집계만 쓴다.
- `se_tasks/s1_swebench`: digest로 고정한 실제 Django 저장소와 공식
  SWE-bench 평가 이미지에서 공개 코드와 숨은 회귀 테스트를 분리한 S1.
- `se_tasks/s1_defect_repair`: runner 단위검증에만 쓰는 소형 S1 장치 과제.
- `se_tasks/s3_production_ops`: 오류율, 부모가 커널에서 받아온 CPU 시간,
  재시작 횟수를 재는 운영 과제. 모든 응답은 오라클이 따로 계산한 정답과
  대조하고, 처리 비용은 같은 실행에서 함께 측정한 참조 구현 대비 비율로
  채점한다. 후보가 쓸 수 있는 수는 어느 것도 점수에 들어가지 않는다.
- `se_tasks/_sandbox/`: 후보를 별도 인터프리터에서 실행하는 채점 경계.
  정답과 카나리와 점수 함수는 부모 프로세스에 남고 자식에 넘어가지 않는다.
- `test_oracle_integrity.py`: 적대 기준선 3종(무동작, 시드, 정답 참조)의
  점수 순서를 강제하는 회귀 테스트. 오라클을 고칠 때마다 돌린다.
- `se_experiment.py`: 게이트 접지 여부와 oracle 숫자 피드백을 분리하는 2x2
  코어와 비용 계측.
- `results/se_smoke_matrix.json`: 스크립트 후보로 장치만 검증한 결과. 연구
  결과가 아니다.

AIDev 예비분석 재현:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements-aidev.txt
    .venv/bin/python analysis/download_aidev.py --data-dir data/aidev
    .venv/bin/python analysis/aidev_pilot.py --data-dir data/aidev --output-dir results/aidev_pilot --sample-size 10000

SE 장치 스모크 검증:

    python3 -m unittest -v test_se_experiment.py
    python3 -m unittest -v test_agent_adapters.py
    python3 se_experiment.py --smoke-output results/se_smoke_matrix.json
    python3 agent_adapters.py --agent codex --task s1 --billing-mode subscription --container-image loop-eng-se-lab-agent:latest --auth-file "$CODEX_AUTH_FILE" --output results/codex_adapter_smoke.json
    python3 agent_adapters.py --agent claude --task s1 --model sonnet --billing-mode subscription --container-image loop-eng-se-lab-agent:latest --auth-file "$CLAUDE_AUTH_FILE" --state-file "$CLAUDE_STATE_FILE" --max-budget-usd 0.25 --output results/claude_adapter_smoke.json

Claude 쿼터 리셋 뒤 두 번째 명령을 한 번 실행해 alias 요청과 별도로
`model_served`에 기록된 런타임 모델 ID를 동결한다. `model_served`가 null이거나
alias면 불변 모델 식별 근거가 아니므로 manifest를 만들지 않는다. 스모크 파일은
장치 검증 기록일 뿐 확증 결과에는 포함하지 않는다.

### 본 측정 러너

`run_measurement.py`는 동결된 manifest를 받아 공통 cycle-1을 공유하는
task-agent-seed 4-cell block 단위로 재개하고, cycle 원시 기록을 append-only
JSONL로 남긴다. 한 분기라도 실패하거나 강제 종료로 block이 불완전하면 기존
시도를 tombstone 처리하고 네 분기를 모두 cycle 1부터 다시 실행한다. 구독 prompt 실행에서는
`incremental_billed_usd`가 항상 0이며, 토큰 기반 API 환산액은 비교용 shadow
telemetry일 뿐 실행 한도가 아니다. 실제 달러 ceiling은 `billing_mode=api`일 때만
작동한다.

Shadow 환산은 manifest에 고정한 출처·조회시점·모델 단가로 재계산한다. 캐시
read와 요청별 long-context 구간을 직접 반영하며, 런타임이 cache write를 구분해
주지 않으면 하한(쓰기 0개)과 보수적 상한(비캐시 입력 전부가 쓰기일 가능성)을
함께 남긴다. `api_equivalent_usd`는 이 보수적 상한이고 실제 청구액이 아니다.

본 측정 전에는 manifest에 다음을 모두 채워야 한다.

- alias가 아닌 두 agent의 정확한 model ID, reasoning effort, 출처와 조회시점까지
  고정한 일반·캐시·cache-write·long-context API 환산 단가
- digest로 고정한 agent/candidate-sandbox image, 실행 timeout, 인증 파일 경로를
  담는 환경변수 이름
- preregistration commit, 다섯 seed, 6 cycles, 네 task와 네 factor cell
- block별 네 cell의 실행 순서를 SHA-256으로 고정 난수화하는 `cell_schedule_seed`
- frozen candidate sandbox image로 실행한 isolation preflight JSON의 경로와 SHA-256
- trajectory별 최대 API 환산 추정치. 이는 초과 계측을 탐지하는 보수적 상한이며
  구독 실행의 실제 청구액이 아니다.

두 측정 Dockerfile은 base image를 OCI digest로, Codex와 Claude Code를 정확한
패키지 버전으로 고정한다. candidate sandbox image에는 held-out oracle, task seed,
정답 또는 점수 함수가 들어가지 않는다. 동결 직전에 이 소스에서 이미지를 다시
빌드하고 최종 로컬 image ID를 manifest에 기록한다.

동결 직전 sandbox image ID로 적대적 preflight를 실행한다. 이 기록은 held-out
소스 부재, network none, read-only root, uid 65534, Linux capability 0을 검사하며
manifest가 파일 SHA-256와 image ID를 다시 대조한다.

    python3 preflight_isolation.py --sandbox-image sha256:<image-id> --output preflight/sandbox-isolation.json

Codex adapter는 CLI 최종 텍스트의 자기보고를 모델 식별 근거로 쓰지 않는다.
컨테이너 안에서 Codex App Server를 시작하고 `thread/start`,
`thread/settings/updated`, `model/rerouted` 프로토콜 사건으로 실제 제공 모델과
reasoning effort를 기록한다. 본 측정에서는 둘 중 하나라도 manifest와 다르거나
런타임이 값을 보고하지 않으면 trajectory를 실패 처리한다. `model/rerouted`가
관측되면 목적 모델을 보존하되 reroute 이력도 원시 로그에 함께 남긴다.
App Server 호출은 기존 ChatGPT 구독 인증을 사용하므로 추가 API 청구액은 0이다.
로그의 `api_equivalent_usd`는 동일 토큰을 API로 실행했을 때의 비교용 환산치다.

먼저 호출 없이 계획을 확인한다.

    python3 run_measurement.py --manifest measurement-manifest.json --log results/confirmatory-cycles.jsonl --run-id confirmatory-01 --plan-only

동결은 자기 참조를 피하기 위해 두 단계로 한다. 정확한 모델·단가·이미지 digest를
모두 채운 tracked template의 `preregistration_commit`에는
`__PREREGISTRATION_FREEZE_COMMIT__`만 둔다. 그 template과 코드 전체를 커밋 F로
만들고 annotated tag `prereg-v1`을 F에 붙인 뒤, 깨끗한 F worktree에서 아래 명령이
실제 manifest를 생성한다. 도구는 lightweight tag, 이동한 HEAD, untracked template,
untracked isolation preflight, 기존 manifest 덮어쓰기를 모두 거부하고 생성 전에
`run_measurement.load_manifest`로 전체 확증 grid를 검증한다.

    python3 finalize_measurement_manifest.py --template measurement-manifest.template.json --output measurement-manifest.json --tag prereg-v1

실행 후에는 원시 로그만으로 결과와 무결성 상태를 재계산한다. HO-A는 gate와
다음-cycle feedback에만 사용되고, 보고 결과는 gate가 보지 못한 HO-B에서 계산된다.

    python3 replay.py --log results/confirmatory-cycles.jsonl --output results/confirmatory-replay.json

## 확증 격리

확증 실행에서 신뢰 주체는 호스트 runner와 host-side oracle이다. 에이전트
컨테이너에는 task workspace와 일회성 구독 인증만 들어가고, held-out 테스트,
workload, oracle 소스, 정답, score store와 분석 파일은 마운트하지 않는다. 에이전트가
반환한 candidate artifact만 신뢰 경계를 건넌 뒤 host-side oracle이 채점한다.

S1은 digest-pinned 공식 SWE-bench 평가 이미지에서, S3 후보 함수는 연구 파일이 없는
별도 digest-pinned sandbox 이미지에서 실행한다. 두 candidate 실행 경로는 네트워크를
끄고 filesystem을 read-only로 둔다. 동결 직전 적대적 preflight가 다음을 직접 확인한다.

- held-out 소스와 알려진 절대 경로가 이미지에 없음
- loopback 외 인터페이스가 없고 외부 network connect가 실패함
- root mount가 read-only이고 실행 uid가 65534임
- effective Linux capability set이 0임

성공한 preflight JSON과 SHA-256, 검사한 sandbox image ID를 frozen manifest에 결박한다.
매 cycle에는 별도로 canary, candidate archive, model identity와 등록된 reward-hacking
guard를 검사한다. 어느 하나라도 실패하면 replay는 clean confirmatory result를 만들지
않는다.

동결 전 감사에서 이전 candidate sandbox image가 `se_tasks`를 복사해 절대 경로로
held-out 소스를 읽을 수 있음을 발견했다. 확증 실행은 시작되지 않아 오염된 확증 결과는
0건이다. 현재 이미지는 연구 파일을 전혀 복사하지 않으며, manifest-selected digest가
실제 호출 시 적용되는지와 위 preflight 결박을 회귀 테스트한다.

## 초기 컨테이너 장치 (확증 아님)

`docker-compose.yml`, `container/`와 `run_isolated.py`는 agentnet/oraclenet 분리와
stub agent로 초기 메커니즘을 검증한 보존 장치다. 아래 실행에서 나오는 3-seed 수치는
장치 스모크 결과일 뿐 사전등록된 확증 결과가 아니다.

    sudo docker compose up -d --build
    python3 run_isolated.py

확증 결과의 권위 경로는 위의 frozen manifest를 받는 `run_measurement.py`와 원시 로그를
재계산하는 `replay.py`다.

## 규칙

- 분석은 표준 라이브러리만. 한국어 카피에 em-dash 없음.
