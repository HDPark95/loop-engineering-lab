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
    python3 agent_adapters.py --agent claude --task s1 --model sonnet --billing-mode subscription --container-image loop-eng-se-lab-agent:latest --auth-file "$CLAUDE_AUTH_FILE" --persist-refreshed-credentials --output results/claude_adapter_smoke.json

Claude 쿼터 리셋 뒤 두 번째 명령을 alias로 한 번 실행해 `model_served`에 기록된
런타임 모델 ID를 찾고, 그 exact ID를 `--model`에 넣어 한 번 더 실행한다. 두 번째
기록의 `model_requested`와 `model_served`가 같은 불변 ID일 때만 manifest에 동결한다.
`model_served`가 null이거나 alias면 불변 모델 식별 근거가 아니므로 manifest를 만들지
않는다. 두 스모크 파일은 장치 검증 기록일 뿐 확증 결과에는 포함하지 않는다.

### 본 측정 러너

`run_measurement.py`는 동결된 manifest를 받아 공통 cycle-1을 공유하는
task-agent-seed 4-cell block 단위로 재개하고, cycle 원시 기록을 append-only
JSONL로 남긴다. 한 분기라도 실패하거나 강제 종료로 block이 불완전하면 기존
시도를 tombstone 처리하고 네 분기를 모두 cycle 1부터 다시 실행한다. 구독 prompt 실행에서는
`incremental_billed_usd`가 항상 0이며, 토큰 기반 API 환산액은 비교용 shadow
telemetry다. 실제 달러 ceiling과 Claude CLI의 `--max-budget-usd`는
`billing_mode=api`일 때만 작동하며 구독 명령에는 그 플래그를 전달하지 않는다.
다만 manifest의 trajectory별 shadow 추정치는
계측 이상을 조기에 탐지하는 보수적 무결성 guard로 사용되며 실제 결제나 API 전환을
뜻하지 않는다.

Shadow 환산은 manifest에 고정한 출처·조회시점·모델 단가로 재계산한다. 캐시
read와 요청별 long-context 구간을 직접 반영하며, 런타임이 cache write를 구분해
주지 않으면 하한(쓰기 0개)과 보수적 상한(비캐시 입력 전부가 쓰기일 가능성)을
함께 남긴다. `api_equivalent_usd`는 이 보수적 상한이고 실제 청구액이 아니다.

셀 순서 seed는 Claude 결과를 보기 전에 PR #13 병합 해시와 성공한 격리 preflight
파일 해시에서 결정적으로 파생해 R24에 공개했다. manifest에는
`ad8b6e46c10c24d5ada9c6797ce15deec26632b26dac14c173ec84ea1c30d369`를 그대로
넣으며 다른 seed를 만들지 않는다.

본 측정 전에는 manifest에 다음을 모두 채워야 한다.

- alias가 아닌 두 agent의 정확한 model ID, reasoning effort, 출처와 조회시점까지
  고정한 일반·캐시·cache-write·long-context API 환산 단가
- digest로 고정한 agent/candidate-sandbox image, 실행 timeout, 인증 파일 경로를
  담는 환경변수 이름. 두 구독 agent entry는 모두
  `persist_refreshed_credentials=true`
- preregistration commit, 다섯 seed, 6 cycles, 네 task와 네 factor cell
- block별 네 cell의 실행 순서를 SHA-256으로 고정 난수화하는 `cell_schedule_seed`
- frozen candidate sandbox image로 실행한 isolation preflight JSON의 경로와 SHA-256
- trajectory별 최대 API 환산 추정치. 이는 초과 계측을 탐지하는 보수적 상한이며
  구독 실행의 실제 청구액이 아니다.

동결 태그 직전 공개 이력 감사는 clean clone에서 실행한다. 현재 tree·커밋 metadata·
모든 HEAD reachable blob의 금지 조직명, 로컬 경로, credential 형식을 검사하며,
문서화된 과거 provenance 문자열 두 blob만 정확한 object ID·경로·개수로 허용한다.

    python3 audit_public_history.py

두 측정 Dockerfile은 base image를 OCI digest로, Codex와 Claude Code를 정확한
패키지 버전으로 고정한다. candidate sandbox image에는 held-out oracle, task seed,
정답 또는 점수 함수가 들어가지 않는다. 동결 직전에 이 소스에서 이미지를 다시
빌드하고 최종 로컬 image ID를 manifest에 기록한다.

동결 직전 sandbox image ID로 적대적 preflight를 실행한다. 이 기록은 held-out
소스 부재, network none, read-only root, uid 65534, Linux capability 0을 검사하며
manifest가 파일 SHA-256와 image ID를 다시 대조한다.

`measurement-manifest.template.json`에는 현재 고정할 수 있는 전체 확증 계약이 들어
있다. `__...__` 값은 Claude exact model·공식 가격과 6-cycle apparatus 상한을 확인한
뒤에만 채운다. finalizer는 이런 runtime placeholder가 하나라도 남으면 tag 결박 전에
거부하고, `__PREREGISTRATION_FREEZE_COMMIT__`만 annotated tag의 commit으로 교체한다.
구독 쿼터 응답은 agent별 단일 writer lane에서 한 시간 간격으로 최대 168회 재확인한다.
따라서 한 agent가 주간 reset을 기다리는 동안 다른 agent lane은 계속 실행하며, 짧은
retry 소진 때문에 대기 중인 전체 block을 연속 폐기하지 않는다.
8월 15일 pre-freeze Claude 6-cycle resource trajectory도
`logs/apparatus/claude-resource-20260815.manifest.template.json`에 미리 고정돼 있다.
exact model과 그 모델의 공식 입력·출력 shadow 단가 세 값만 확인 후 채운다.
alias/exact-ID adapter smoke JSON은 최초 증거를 보존하기 위해 기존 출력 경로를 절대
덮어쓰지 않는다. 재시도가 필요하면 새 run ID가 드러나는 새 파일명을 사용한다.
두 smoke와 공식 가격 JSON을 확보한 뒤 아래 도구로 apparatus manifest를 만든다.
도구는 alias와 exact-ID가 같은 불변 모델을 제공했고 두 실행·공개 테스트·credential
scan이 성공했는지 확인하며, 기존 출력은 덮어쓰지 않는다.

    python3 prepare_runtime_manifests.py apparatus \
      --alias-smoke logs/apparatus/claude-sonnet-alias-smoke-20260815.json \
      --exact-smoke logs/apparatus/claude-exact-smoke-20260815.json \
      --pricing logs/apparatus/claude-official-pricing-20260815.json \
      --template logs/apparatus/claude-resource-20260815.manifest.template.json \
      --output logs/apparatus/claude-resource-20260815.manifest.json

    python3 preflight_isolation.py --sandbox-image sha256:<image-id> --output preflight/sandbox-isolation.json

Claude와 Codex의 구독 OAuth access token은 장시간 실행 중 갱신될 수 있다. 사용자
원본을 직접 쓰지 말고 각각 mode 0600인 runner 전용 credential file을 만들어
`auth_file_env`가 그 경로를 가리키게 한다. 두 agent 모두 refresh writer를 하나로
제한해 agent별 한 lane에서 실행한다. 최대 동시성 상한은 3이지만 실제 agent 호출
동시성은 Claude 1 + Codex 1이다. 각 호출은 이 파일의 일회성 사본만 보며, Claude에는
추가로 `/workspace` 신뢰·비대화형 권한 확인만 담은 최소 상태를
새로 생성해 마운트한다. 사용자 홈의 전체 Claude 상태 파일은 manifest와 adapter가
거부하므로 그 파일에 캐시된 계정·조직·로컬 프로젝트 metadata와 경로는 직접
노출되지 않는다. 단, OAuth profile scope를 통해 CLI가 조회할 수 있는 식별 metadata는
credential 경계의 잔여위험이며 exact-secret scan의 탐지 범위가 아니다. CLI가
토큰을 갱신했을 때 account metadata와 스키마가 그대로이고 access token 만료와
refresh 시각이 전진한 OAuth record만 원자적으로 각 전용 파일에 반영한다. 원시 token 값은 측정 로그에
기록하지 않는다. 매 호출은 실행 전후 credential의 exact secret 값을 stdout, stderr와
candidate tree에서 byte scan하고, 하나라도 나오면 archive와 cycle log 작성 전에 해당
시도를 폐기한다. replay와 reward-hacking audit은 이 scan의 성공 플래그가 없으면 fail
closed한다.

Codex adapter는 CLI 최종 텍스트의 자기보고를 모델 식별 근거로 쓰지 않는다.
컨테이너 안에서 Codex App Server를 시작하고 `thread/start`,
`thread/settings/updated`, `model/rerouted` 프로토콜 사건으로 실제 제공 모델과
reasoning effort를 기록한다. 본 측정에서는 둘 중 하나라도 manifest와 다르거나
런타임이 값을 보고하지 않으면 trajectory를 실패 처리한다. `model/rerouted`가
관측되면 목적 모델을 보존하되 reroute 이력도 원시 로그에 함께 남긴다.
App Server 호출은 기존 ChatGPT 구독 인증을 사용하므로 추가 API 청구액은 0이다.
로그의 `api_equivalent_usd`는 동일 토큰을 API로 실행했을 때의 비교용 환산치다.

동결 전 1개 6-cycle apparatus trajectory의 자원을 실측할 때는 runner를 background로
시작하고 aggregate-only monitor를 그 PID에 결박한다. monitor는 모델 출력, container
filesystem·환경·명령을 읽지 않고 Docker CPU/memory counter와 host `/proc` counter만
기록한다. 결과에는 container 이름도 남기지 않는다. runner와 monitor 둘 중 하나라도
실패하면 자원 기록을 수용하지 않는다.

    python3 run_measurement.py --manifest logs/apparatus/claude-resource-20260815.manifest.json --log logs/apparatus/claude-resource-20260815.cycles.jsonl --run-id claude-resource-20260815 &
    runner_pid=$!
    python3 monitor_resources.py --pid "$runner_pid" --poll-seconds 1 --output logs/apparatus/claude-resource-20260815.resources.json
    monitor_status=$?
    wait "$runner_pid"
    runner_status=$?
    test "$monitor_status" -eq 0 && test "$runner_status" -eq 0

성공한 Claude apparatus의 정확한 cache-write TTL token을 공식 전체 가격표로 다시
계산한다. trajectory별 shadow 무결성 guard는 결과를 보기 전에 공개한 규칙
`ceil(max(20, 4 × Claude 전 요청 long-context 상한, 4 × Codex 기준 전 요청
long-context 상한))`으로만 정한다. Codex 기준 상한은 이미 기록된 6-cycle S1 shadow
합계에 2.5를 곱한다. 이 값과 가격·smoke·두 apparatus log의 SHA-256은 별도 증거
JSON에 남고, 둘 중 어느 출력도 기존 파일을 덮어쓰지 않는다.

    python3 prepare_runtime_manifests.py confirmatory \
      --alias-smoke logs/apparatus/claude-sonnet-alias-smoke-20260815.json \
      --exact-smoke logs/apparatus/claude-exact-smoke-20260815.json \
      --pricing logs/apparatus/claude-official-pricing-20260815.json \
      --claude-manifest logs/apparatus/claude-resource-20260815.manifest.json \
      --claude-log logs/apparatus/claude-resource-20260815.cycles.jsonl \
      --claude-resources logs/apparatus/claude-resource-20260815.resources.json \
      --template measurement-manifest.template.json \
      --output measurement-manifest.runtime.template.json \
      --evidence-output logs/apparatus/runtime-shadow-budget-20260815.json

동결은 자기 참조를 피하기 위해 두 단계로 한다. 정확한 모델·단가·이미지 digest를
모두 채운 tracked template의 `preregistration_commit`에는
`__PREREGISTRATION_FREEZE_COMMIT__`만 둔다. 그 template과 코드 전체를 커밋 F로
만들고 annotated tag `prereg-v1`을 F에 붙인 뒤, 깨끗한 F worktree에서 아래 명령이
실제 manifest를 생성한다. 도구는 lightweight tag, 이동한 HEAD, untracked template,
untracked isolation preflight, 기존 manifest 덮어쓰기를 모두 거부하고 생성 전에
`run_measurement.load_manifest`로 전체 확증 grid를 검증한다.

    python3 finalize_measurement_manifest.py --template measurement-manifest.runtime.template.json --output measurement-manifest.json --tag prereg-v1

finalizer가 동결 commit을 결박한 뒤, 실제 agent 호출 없이 계획부터 확인한다.

    python3 run_measurement.py --manifest measurement-manifest.json --log results/confirmatory-cycles.jsonl --run-id confirmatory-01 --plan-only

첫 확증 호출 전에는 태그만으로 끝내지 않고 외부 동결 타임스탬프를 만든다. 아래
builder는 annotated tag와 현재 HEAD, 최종 manifest, R35 입력·digest, 격리 preflight를
다시 확인하고, 태그 커밋에 결박한 public-history audit까지 재실행한 뒤 단일
deterministic ZIP과 sidecar SHA-256, Zenodo metadata를 만든다. 공개 검증 게이트는
ZIP 내부 audit의 크기·SHA-256·태그 커밋·0개 unexpected finding을 다시 검사한다.
Zenodo에는 이 ZIP 하나만 별도 공개 record로 발행하며, record의 공개 UTC가 첫 cycle의
`wall_clock_utc`보다 앞서야 한다. 결과가 들어가는 confirmatory replication record와
DOI를 재사용하지 않는다.

    python3 build_preregistration_bundle.py \
      --manifest measurement-manifest.json \
      --runtime-template measurement-manifest.runtime.template.json \
      --runtime-evidence logs/apparatus/runtime-shadow-budget-20260815.json \
      --alias-smoke logs/apparatus/claude-sonnet-alias-smoke-20260815.json \
      --exact-smoke logs/apparatus/claude-exact-smoke-20260815.json \
      --pricing logs/apparatus/claude-official-pricing-20260815.json \
      --claude-manifest logs/apparatus/claude-resource-20260815.manifest.json \
      --claude-log logs/apparatus/claude-resource-20260815.cycles.jsonl \
      --claude-resources logs/apparatus/claude-resource-20260815.resources.json \
      --output-dir release/loop-engineering-preregistration-v1

builder 뒤에는 먼저 로컬 요청 JSON만 만든다. Zenodo 상태를 바꾸는 `create-draft`는
명시적인 production 확인과 `ZENODO_TOKEN`이 있어야 하며, 되돌릴 수 없는 `publish`는
그에 더해 예약된 record ID와 고정 ZIP SHA-256을 명령행에서 다시 일치시켜야 한다.
발행 도구는 공개 record의 단일 파일을 다시 내려받아 SHA-256까지 일치하는 경우에만
외부 타임스탬프 증거 JSON을 만든다. 승인 없이 `create-draft`나 `publish`를 실행하지 않는다.

    python3 zenodo_preregistration.py prepare \
      --bundle-dir release/loop-engineering-preregistration-v1 \
      --publication-date 2026-08-15 \
      --output release/loop-engineering-preregistration-v1/zenodo-request.json

    python3 zenodo_preregistration.py create-draft \
      --request release/loop-engineering-preregistration-v1/zenodo-request.json \
      --bundle release/loop-engineering-preregistration-v1/loop-engineering-preregistration-v1.zip \
      --output release/loop-engineering-preregistration-v1/zenodo-draft-receipt.json \
      --confirm-production zenodo.org

    python3 zenodo_preregistration.py publish \
      --request release/loop-engineering-preregistration-v1/zenodo-request.json \
      --receipt release/loop-engineering-preregistration-v1/zenodo-draft-receipt.json \
      --bundle release/loop-engineering-preregistration-v1/loop-engineering-preregistration-v1.zip \
      --output release/loop-engineering-preregistration-v1/zenodo-public-evidence.json \
      --confirm-record-id <reserved-record-id> \
      --confirm-bundle-sha256 <frozen-zip-sha256>

공개 증거를 독립적으로 다시 검사할 때는 token 없이 `verify-public`을 실행한다. 이 검증의
성공 UTC보다 뒤에만 `run_measurement.py`의 첫 non-plan cycle을 시작한다.

    python3 zenodo_preregistration.py verify-public \
      --request release/loop-engineering-preregistration-v1/zenodo-request.json \
      --receipt release/loop-engineering-preregistration-v1/zenodo-draft-receipt.json \
      --bundle release/loop-engineering-preregistration-v1/loop-engineering-preregistration-v1.zip \
      --output release/loop-engineering-preregistration-v1/zenodo-public-recheck.json

schema 6 runner는 절차 문구만 믿지 않는다. non-plan 확증 실행에는 공개 증거와 그 증거가
가리키는 ZIP을 함께 요구하고, ZIP 내부의 preregistration commit·measurement manifest
digest까지 현재 manifest와 일치시키기 전에는 로그 파일을 만들지 않는다. 검증된 DOI와
두 SHA-256, 공개 검증 UTC는 모든 cycle·abandonment row에 복사된다.

    python3 run_measurement.py \
      --manifest measurement-manifest.json \
      --log results/confirmatory-cycles.jsonl \
      --run-id confirmatory-01 \
      --preregistration-evidence release/loop-engineering-preregistration-v1/zenodo-public-evidence.json \
      --preregistration-bundle release/loop-engineering-preregistration-v1/loop-engineering-preregistration-v1.zip

실행 후에는 원시 로그만으로 결과와 무결성 상태를 재계산한다. HO-A는 gate와
다음-cycle feedback에만 사용되고, 보고 결과는 gate가 보지 못한 HO-B에서 계산된다.

    python3 replay.py --log results/confirmatory-cycles.jsonl --archive-root artifacts/confirmatory --output results/confirmatory-replay.json
    python3 analysis/classify_reward_hacking.py --log results/confirmatory-cycles.jsonl --output results/confirmatory-reward-hacking.json
    python3 analysis/fit_clustered.py --log results/confirmatory-cycles.jsonl --archive-root artifacts/confirmatory --output results/confirmatory-analysis.json

`fit_clustered.py`는 replay 무결성과 reward-hacking audit을 같은 원시 로그에서 다시
실행하며, 둘 중 하나라도 clean하지 않으면 분석을 거부한다. 수용된 분석 JSON에는
원시 로그의 SHA-256과 전체 audit 결과가 포함되므로, 이후 원고 renderer와 제출
manifest가 원시 측정 로그까지 digest chain으로 결박할 수 있다.
분석 시작 전후의 로그 SHA-256이 달라지면 동시 append로 간주해 출력을 거부하므로,
runner가 완전히 종료된 안정된 원시 로그에서만 확증 분석을 실행한다.
replay와 분석은 각 cycle이 참조하는 archive manifest와 모든 content-addressed object의
경로·크기·SHA-256도 `--archive-root`에서 다시 확인하며, 파일 하나라도 없거나 바뀌면
clean 결과를 만들지 않는다.
standalone replay와 reward-hacking JSON도 같은 원시 로그 SHA-256을 기록하고 읽기
전후 해시가 다르면 clean을 거부한다. 분석 schema 3은 이 안정성 결과와 물리 archive
검증을 포함하며, 원고 renderer는 이전 schema를 받지 않는다.

확증 결과와 원고 렌더링을 검증한 뒤 공개용 replication bundle을 만든다. 도구는
`prereg-v1` 소스, frozen manifest·preflight, 원시 로그와 세 결과 JSON, 그리고 로그가
실제로 참조하는 candidate manifest/object만 묶는다. 160 trajectories·960 completed
logical rows, 동일 manifest/tag/log digest, clean replay/audit/analysis를 다시 확인하며
기존 output directory는 덮어쓰지 않는다.

    python3 build_replication_bundle.py \
      --manifest measurement-manifest.json \
      --log results/confirmatory-cycles.jsonl \
      --replay results/confirmatory-replay.json \
      --reward-audit results/confirmatory-reward-hacking.json \
      --analysis results/confirmatory-analysis.json \
      --archive-root artifacts/confirmatory \
      --preregistration-evidence release/loop-engineering-preregistration-v1/zenodo-public-evidence.json \
      --preregistration-bundle release/loop-engineering-preregistration-v1/loop-engineering-preregistration-v1.zip \
      --output-dir release/loop-engineering-confirmatory-v1

같은 fail-closed client는 post-outcome replication record에도 별도 role과 상태를 사용한다.
먼저 로컬 요청만 만들고, production 승인을 받은 뒤 draft를 생성해 DOI를 예약한다. 이
예약 DOI를 원고·Title Page·cover letter에 결박하고 최종 제출 일습을 검증한 뒤에만,
별도의 publish 승인 아래 정확한 record ID와 ZIP SHA-256으로 발행한다.

    python3 zenodo_preregistration.py prepare-replication \
      --bundle-dir release/loop-engineering-confirmatory-v1 \
      --publication-date <YYYY-MM-DD> \
      --output release/loop-engineering-confirmatory-v1/zenodo-request.json

    python3 zenodo_preregistration.py create-draft \
      --request release/loop-engineering-confirmatory-v1/zenodo-request.json \
      --bundle release/loop-engineering-confirmatory-v1/loop-engineering-confirmatory-v1.zip \
      --output release/loop-engineering-confirmatory-v1/zenodo-draft-receipt.json \
      --confirm-production zenodo.org

    python3 zenodo_preregistration.py publish \
      --request release/loop-engineering-confirmatory-v1/zenodo-request.json \
      --receipt release/loop-engineering-confirmatory-v1/zenodo-draft-receipt.json \
      --bundle release/loop-engineering-confirmatory-v1/loop-engineering-confirmatory-v1.zip \
      --output release/loop-engineering-confirmatory-v1/zenodo-public-evidence.json \
      --confirm-record-id <reserved-record-id> \
      --confirm-bundle-sha256 <final-zip-sha256>

출력 directory에는 Zenodo에 단일 파일로 올릴 deterministic
`loop-engineering-confirmatory-v1.zip`, 그 sidecar SHA-256, API/UI 입력용 metadata가
생긴다. ZIP 내부에는 source/candidate tarball, 외부 사전등록 공개 증거,
`replication-manifest.json`, `SHA256SUMS`, 두 저자 ORCID와 preprint 관계를 담은
metadata가 포함된다. builder는 모든 schema 6 log row의 사전등록 DOI·증거 SHA·bundle
SHA가 이 공개 증거와 같은지도 다시 확인한다. Zenodo의
manual software deposit 지침에 따라 record에는 이 ZIP 하나만 업로드한다.

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
