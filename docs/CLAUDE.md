# docs/ — 설계 문서

## 파일

| 파일 | 내용 | 분량 |
|------|------|------|
| `monitoring-design.md` | 모니터링 아키텍처 설계. 14개 섹션: 계측(Instrumentation), 메트릭, 로그, 트레이스, 대시보드, 알림, OTel Collector, 캐시/쿼터 모니터링, 컨테이너 구성, 장애 대응 시나리오 | 553줄 |
| `observability-guide.md` | Grafana 관측성 가이드. TraceQL/PromQL/LogQL 쿼리 예시, Span Attributes 목록, 대시보드 활용법, 데이터소스 연동, K8s 환경 참고사항 | 352줄 |

## 용도

- `monitoring-design.md`: 설계 시점 스냅샷. 왜 이렇게 만들었는지의 근거 문서
- `observability-guide.md`: 운영자/개발자가 Grafana에서 데이터를 찾을 때 참고하는 실용 가이드
