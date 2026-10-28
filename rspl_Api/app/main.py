import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import DatabaseBusyError, warm_pool
from app.routers import (
    admin_content,
    admin_customers,
    admin_face,
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
    enghub_activities,
    enghub_attachments,
    enghub_decisions,
    enghub_developer_dashboard,
    enghub_devitems,
    enghub_features,
    enghub_masters,
    enghub_menu_rights,
    enghub_releases,
    enghub_reports,
    enghub_tasks,
    general,
    general_attendance_sheet,
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
    support_sentimeter,
    support_tt,
    support_visitlog,
    user_rights,
    vote,
)

app = FastAPI(title="RSPL API", version="0.1.0")

# Serve face reference photos uploaded during enrolment.
_face_photos_dir = os.path.join(os.path.dirname(__file__), "..", "face_photos")
os.makedirs(_face_photos_dir, exist_ok=True)
app.mount("/face-photos", StaticFiles(directory=_face_photos_dir), name="face-photos")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Starlette's CORS default exposes no custom response headers to
    # cross-origin JS at all (the browser receives them over the wire, but
    # fetch/XHR's Headers API can't see them) — Content-Disposition needs to
    # be readable client-side so file-download flows like
    # support_callsip.download_call_recording can use the server's real
    # filename instead of inventing their own.
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(developer.router)
app.include_router(vote.router)
app.include_router(gst.router)
app.include_router(general.router)
app.include_router(general_attendance_sheet.router)
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
app.include_router(support_sentimeter.router)
app.include_router(support_executive_schedule.router)
app.include_router(support_tt.router)
app.include_router(admin_content.router)
app.include_router(admin_face_inout.router)
app.include_router(admin_face.router)
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
app.include_router(enghub_masters.router)
app.include_router(enghub_features.router)
app.include_router(enghub_devitems.router)
app.include_router(enghub_tasks.router)
app.include_router(enghub_activities.router)
app.include_router(enghub_decisions.router)
app.include_router(enghub_releases.router)
app.include_router(enghub_attachments.router)
app.include_router(enghub_reports.router)
app.include_router(enghub_developer_dashboard.router)
app.include_router(enghub_menu_rights.router)


@app.exception_handler(DatabaseBusyError)
def _database_busy(request: Request, exc: DatabaseBusyError) -> JSONResponse:
    # A fast, explicit "try again shortly" instead of a request that hangs
    # until the proxy gives up — see _POOL_WAIT_SECONDS in app/db.py.
    return JSONResponse(
        status_code=503,
        content={"detail": "The server is busy right now. Please try again in a minute."},
        headers={"Retry-After": "30"},
    )


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
