# loop-engineering-lab

[[loop-engineering-paper]] 의 측정 하니스. 평가자의 정보 채널 타입(in-band 트랜스크립트 대 out-of-band world-state 오라클)이 자율 루프의 자기기만(progress mirage)과 실세계 산출에 미치는 인과효과를 측정한다. 설계 근거는 paper 레포의 lab-design.md 와 RESEARCH-CORE.md.

## 현재 단계: 측정 장치 검증 (MVP 골격)

docker 없이 두 신뢰 구역을 파일시스템 경계로 분리한다.
- 에이전트 구역: `app_template` 를 복사한 작업본. 에이전트(여기서는 스크립트 stub)는 이 디렉토리만 편집한다.
- 오라클 구역: `simulator.py` 가 배포된 산출물의 실제 속성(로드 바이트, 퍼널 단계 수, CTA 유무, 카피 길이)으로 전환을 계산하고, 에이전트가 닿을 수 없는 `oracle/*.db` 에 기록한다.

`loop.py` 는 같은 stub 에이전트로 두 arm 을 돌린다. 유일한 차이는 사이클을 게이트하는 평가자다.
- in-band-self: 에이전트의 주장만 보고 수용(stub 은 매 사이클 개선했다고 주장 = 자기기만).
- out-of-band: 오라클의 실제 전환 델타가 양일 때만 수용.

progress-mirage rate = 수용된 사이클 중 오라클 델타가 0 이하인 비율.

## 실행

    python3 loop.py

stub 데모는 in-band-self 의 mirage rate 가 높고 out-of-band 가 0 에 가까움을 보인다. 즉 측정 장치가 자기기만을 정확히 잡아낸다. 이 단계는 측정의 타당성을 검증할 뿐 연구 결과가 아니다.

## 다음 (게이트됨)

- stub 을 frontier LLM 실제 에이전트로 교체(반응형). out-of-band 가 실세계 산출을 더 만드는지(real-outcome-at-budget, time-to-first-real-outcome) 측정.
- in-band-judge arm + judge 품질 ablation 추가.
- world-state 두 번째 오라클 T3(서버측 사건 로그), 경계 대조군 B1 추가.
- 실제 에이전트 파일럿은 공유 모델 quota 를 쓰므로 조용한 전용 호스트에서 실행.

## 규칙

- 분석은 표준 라이브러리만. 회사 컨텍스트 없음. 한국어 카피에 em-dash 없음.
