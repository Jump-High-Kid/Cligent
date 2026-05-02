"""
src/routers/ — Cligent 라우터 패키지

main.py 4,000줄 분할의 결과. 6개 도메인 라우터:
  - auth.py     로그인·온보딩·invite·login_history·공개 페이지·beta apply
  - clinic.py   /settings·staff·clinic profile·blog config·rbac·modules
  - billing.py  plan/usage (M1+ 결제 라우트 자리)
  - blog.py     /blog·/api/blog/*·/api/blog-chat/*·/api/image/*·legacy generators·agents
  - dashboard.py /dashboard·/help·feedback·announcements(read)
  - admin.py    /admin/*·/api/admin/*·announcements(write)·beta applicants 관리

각 라우터는 `router = APIRouter()` 를 노출하고 main.py 의 `app.include_router(...)` 로 등록.
공용 의존성은 `src/dependencies.py` 참조.
"""
