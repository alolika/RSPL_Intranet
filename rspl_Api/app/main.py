from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import warm_pool
from app.routers import (
    admin_content,
    admin_customers,
    admin_face_inout,
    admin_holiday,
    admin_ledger,
    admin_leave,
    admin_reports,
    agile_appreciation,
    agile_backlog,
    agile_dashboard,
    agile_masters,
    agile_projects,
    agile_tasks,
    agile_test,
    admin_vouchers,
    auth,
    developer,
    general,
    general_dashboards,
    general_leave,
    general_mycaller,
    general_org,
    general_reports,
    general_schedule,
    gst,
    marketing,
    marketing_enquiry,
    marketing_geo,
    marketing_lead,
    marketing_ops,
    marketing_referral,
    marketing_register,
    marketing_registration,
    marketing_reports,
    marketing_tour,
    recruitment,
    recruitment_auth,
    support,
    support_amc,
    support_callsip,
    support_executive_schedule,
    support_modules,
    support_tt,
    support_visitlog,
    user_rights,
    vote,
)

app = FastAPI(title="RSPL API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(developer.router)
app.include_router(vote.router)
app.include_router(gst.router)
app.include_router(general.router)
app.include_router(general_dashboards.router)
app.include_router(general_leave.router)
app.include_router(general_schedule.router)
app.include_router(general_org.router)
app.include_router(general_mycaller.router)
app.include_router(general_reports.router)
app.include_router(support.router)
app.include_router(support_visitlog.router)
app.include_router(support_callsip.router)
app.include_router(support_amc.router)
app.include_router(support_modules.router)
app.include_router(support_executive_schedule.router)
app.include_router(support_tt.router)
app.include_router(admin_content.router)
app.include_router(admin_face_inout.router)
app.include_router(admin_holiday.router)
app.include_router(admin_vouchers.router)
app.include_router(admin_customers.router)
app.include_router(admin_reports.router)
app.include_router(admin_leave.router)
app.include_router(admin_ledger.router)
app.include_router(user_rights.router)
app.include_router(marketing.router)
app.include_router(marketing_enquiry.router)
app.include_router(marketing_register.router)
app.include_router(marketing_registration.router)
app.include_router(marketing_reports.router)
app.include_router(marketing_ops.router)
app.include_router(marketing_lead.router)
app.include_router(marketing_geo.router)
app.include_router(marketing_tour.router)
app.include_router(marketing_referral.router)
app.include_router(recruitment_auth.router)
app.include_router(recruitment.router)
app.include_router(agile_masters.router)
app.include_router(agile_projects.router)
app.include_router(agile_tasks.router)
app.include_router(agile_backlog.router)
app.include_router(agile_test.router)
app.include_router(agile_dashboard.router)
app.include_router(agile_appreciation.router)


@app.on_event("startup")
def _warm_db_pool() -> None:
    # Pays the DB connection pool's expensive first-connect cost (100+
    # seconds, confirmed live — see app/db.py) once here, while the server is
    # starting up, instead of on whichever user's request happens to be the
    # first one to touch the database.
    warm_pool()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
