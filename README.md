> **Public replication package for the loop-engineering study.**

# loop-engineering-lab

[[loop-engineering-paper]] 의 측정 하니스. 평가자의 정보 채널 타입(in-band 트랜스크립트 대 out-of-band world-state 오라클)이 자율 루프의 자기기만(progress mirage)과 실세계 산출에 미치는 인과효과를 측정한다. 설계 근거는 paper 레포의 lab-design.md 와 RESEARCH-CORE.md.

공개 데이터와 재현 범위는 [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md),
SE 특별호용 개정 설계와 동결 조건은 [PREREGISTRATION.md](PREREGISTRATION.md)에 있다.

## 2026-08 SE 확장

- `analysis/aidev_pilot.py`: AIDev 10,000-PR 탐색적 타당성 분석. 원문이나
  식별자를 내보내지 않고 집계만 쓴다.
- `se_tasks/s1_defect_repair`: 공개 테스트와 held-out 회귀 테스트를 분리한
  결함 수정 과제.
- `se_tasks/s3_production_ops`: 오류율, p95 논리 지연, 재시작 횟수를 재는
  운영 과제.
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
    python3 agent_adapters.py --agent codex --task s1 --container-image loop-eng-se-lab-agent:latest --auth-file "$CODEX_AUTH_FILE" --output results/codex_adapter_smoke.json
    python3 agent_adapters.py --agent claude --task s1 --model sonnet --container-image loop-eng-se-lab-agent:latest --auth-env ANTHROPIC_API_KEY --max-budget-usd 0.25 --output results/claude_adapter_smoke.json

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
