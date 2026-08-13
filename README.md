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
- digest로 고정한 agent/oracle container image, 실행 timeout, 인증 파일 경로를
  담는 환경변수 이름
- preregistration commit, 다섯 seed, 6 cycles, 네 task와 네 factor cell
- block별 네 cell의 실행 순서를 SHA-256으로 고정 난수화하는 `cell_schedule_seed`
- trajectory별 최대 API 환산 추정치. 이는 초과 계측을 탐지하는 보수적 상한이며
  구독 실행의 실제 청구액이 아니다.

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
기존 manifest 덮어쓰기를 모두 거부하고 생성 전에 `run_measurement.load_manifest`로
전체 확증 grid를 검증한다.

    python3 finalize_measurement_manifest.py --template measurement-manifest.template.json --output measurement-manifest.json --tag prereg-v1

실행 후에는 원시 로그만으로 결과와 무결성 상태를 재계산한다. HO-A는 gate와
다음-cycle feedback에만 사용되고, 보고 결과는 gate가 보지 못한 HO-B에서 계산된다.

    python3 replay.py --log results/confirmatory-cycles.jsonl --output results/confirmatory-replay.json

## 격리 (실측으로 증명, 가정 아님)

논문의 핵심 주장은 "에이전트가 오라클을 게이밍할 수 없다"이다. 이를 파일시스템 관행이 아니라 실제 컨테이너 격리로 보장한다.

- 두 Docker 네트워크 agentnet, oraclenet 사이에 라우트가 없다. 에이전트 컨테이너는 agentnet 에만, 오라클 컨테이너는 oraclenet 에만 붙는다. 쉘과 curl 을 다 줘도 에이전트는 오라클에 닿을 수 없다.
- 공유 표면은 artifact 볼륨(에이전트 배포물) 하나뿐이고 오라클은 이를 read-only 로만 읽는다. 오라클 store 는 오라클 컨테이너 내부에 있고 어떤 공유 볼륨에도 없다.
- 신뢰 주체는 호스트의 runner(실험자)다. runner 는 양쪽에 docker exec 로 접근해 측정만 한다. 게이밍 대상인 에이전트만 격리된다.

### adversarial 경계 증명

오라클은 비밀 canary 를 oraclenet:8000 에 서빙한다. 신뢰 호스트는 canary 를 가져오고(서버가 살아있음 증명), 에이전트는 오라클의 정확한 IP 를 줘도 못 가져온다(no route). 실행 예:

    [host  -> oracle:8000] canary retrieved: True
    [agent -> oracle:8000] canary retrieved: False   (empty: no route)
    [agent -> /oracle  fs] canary leaked:    False   (No such file)
    >>> ISOLATION HOLDS: True

canary 문자열이 에이전트 측 출력에 나타나면 격리가 깨진 것이다. 이 속성을 매 실행 계측한다.

## 실행

    sudo docker compose up -d --build
    python3 run_isolated.py

run_isolated.py 가 먼저 경계 증명을 돌리고, 이어 두 arm(in-band-self 대 out-of-band)을 격리 하에서 측정한다. 현재 스크립트 stub 에이전트 기준 결과(시드 3개): in-band-self mirage 0.75, out-of-band 0.00. 즉 측정 장치와 격리가 모두 작동한다. 이 단계는 측정과 격리의 타당성 검증이지 연구 결과가 아니다.

## 구성

- `docker-compose.yml` : 두 네트워크 격리, artifact 볼륨, agent/oracle 컨테이너.
- `container/sim_core.py` : 전환 모델. `container/sim_step.py` : 오라클 안에서 산출물 채점 후 store 기록.
- `container/agent_edit.py` : stub 에이전트 편집 + 자기보고(claim). `container/canary_server.py` : 경계 증명용 canary.
- `run_isolated.py` : 호스트 runner(경계 증명 + arm 측정).
- `simulator.py`, `loop.py` : docker 없이 도는 측정 로직 단위검증(참고용). 권위 실행은 컨테이너판.

## 다음 (게이트됨)

- stub 을 frontier LLM 실제 에이전트로 교체(반응형). agent 컨테이너 안에서 claude CLI 를 띄워 같은 격리 하에 돌린다. out-of-band 가 실세계 산출을 더 만드는지(real-outcome-at-budget, time-to-first-real-outcome) 측정.
- in-band-judge arm + judge 품질 ablation. world-state 두 번째 오라클 T3(서버측 사건 로그), 경계 대조군 B1.
- 시드와 모델 확장, PREREGISTRATION 동결 후 본 측정. 실제 에이전트 파일럿은 공유 모델 quota 를 쓰므로 전용 호스트 권장.

## 규칙

- 분석은 표준 라이브러리만. 한국어 카피에 em-dash 없음.
