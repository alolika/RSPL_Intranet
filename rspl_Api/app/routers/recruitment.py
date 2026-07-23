"""Mirrors Section_Recruitment's 14 pages (excluding Login.aspx, handled by
recruitment_auth.py, and Default/LogOut/DashBoard, which need no backend
calls at all — DashBoard just displays the login session's own fields).

Real finding: rspl_DiscProfile's MostAnswer/LeastAnswer columns are DISC
letter codes ('S'/'I'/'C'/'D'/'-'), not numeric SrNo values despite the VB
source's misleadingly-named `mostsrno`/`Leastsrno` local variables in
DISCProfile.aspx.vb's btnSubmit_Click. The Angular DiscAnswer contract
(qualityNo/mostSrNo/leastSrNo, both ints 1-4 identifying which of the 4
items in a group was picked) only needs to track UI selection — this router
resolves the actual letter codes via rspl_DiscProfile(QualityNo,
QualitySrNo) lookups before inserting into rspl_DiscAnswerDetail, so no
Angular-side changes were needed to carry this through correctly.
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_cursor, rows_to_dicts
from app.deps import CurrentCandidate, get_current_candidate
from app.security import legacy_password_obfuscate

router = APIRouter(prefix="/recruitment", tags=["recruitment"])


class LookupOption(BaseModel):
    label: str
    value: int


# -------------------- DISCProfile --------------------


class DiscQualityItem(BaseModel):
    quality_sr_no: int
    quality_english: str
    quality_marathi: str


class DiscQualityGroup(BaseModel):
    quality_no: int
    items: list[DiscQualityItem]


class DiscAnswerIn(BaseModel):
    quality_no: int
    most_sr_no: int | None
    least_sr_no: int | None


class SubmitDiscAnswersRequest(BaseModel):
    answers: list[DiscAnswerIn]


@router.get("/disc-quality-groups", response_model=list[DiscQualityGroup])
def get_disc_quality_groups() -> list[DiscQualityGroup]:
    with get_cursor() as cursor:
        cursor.execute(
            "Select QualityNo, QualitySrNo, QualityEnglish, QualityMarathi From rspl_DiscProfile Order By QualityNo, QualitySrNo"
        )
        rows = rows_to_dicts(cursor)
    groups: dict[int, DiscQualityGroup] = {}
    for r in rows:
        g = groups.setdefault(r["QualityNo"], DiscQualityGroup(quality_no=r["QualityNo"], items=[]))
        g.items.append(
            DiscQualityItem(
                quality_sr_no=r["QualitySrNo"], quality_english=r["QualityEnglish"] or "",
                quality_marathi=r["QualityMarathi"] or "",
            )
        )
    return list(groups.values())


@router.post("/disc-answers")
def submit_disc_answers(body: SubmitDiscAnswersRequest, current: CurrentCandidate = Depends(get_current_candidate)) -> dict:
    with get_cursor() as cursor:
        cursor.execute("Delete From rspl_DiscAnswerDetail where Candidateid = ?", current.candidate_id)
        for a in body.answers:
            most_ans = ""
            least_ans = ""
            if a.most_sr_no is not None:
                cursor.execute(
                    "Select MostAnswer From rspl_DiscProfile Where QualityNo = ? And QualitySrNo = ?",
                    a.quality_no, a.most_sr_no,
                )
                row = cursor.fetchone()
                most_ans = row[0] if row else ""
            if a.least_sr_no is not None:
                cursor.execute(
                    "Select LeastAnswer From rspl_DiscProfile Where QualityNo = ? And QualitySrNo = ?",
                    a.quality_no, a.least_sr_no,
                )
                row = cursor.fetchone()
                least_ans = row[0] if row else ""
            cursor.execute(
                "insert into rspl_DiscAnswerDetail(CandidateID, QualityNo, QltyMostAns, QltyLeastAns) values (?, ?, ?, ?)",
                current.candidate_id, a.quality_no, most_ans, least_ans,
            )
        _refresh_disc_answer_summary(cursor, current.candidate_id)
    return {"success": True}


def _refresh_disc_answer_summary(cursor, candidate_id: int) -> None:
    # Confirmed live: WebProc_DiscAnswerSummary's unconditional
    # `Insert into rspl_DiscAnswerSummary Select ... From rspl_discquality`
    # has no explicit column list and only supplies 8 values, but the table
    # has 9 columns (IsSynctoCloud was added after this proc was written) —
    # "Column name or number of supplied values does not match table
    # definition" for EVERY candidate, a genuine pre-existing production bug
    # (not something introduced by this migration; the real ASP.NET app
    # hits this identical SQL error today). Not fixable from here (can't
    # alter production stored procs) — degrades gracefully by reading
    # whatever summary rows already exist instead of 500ing the whole page,
    # same graceful-individual-failure treatment used for
    # SupportCoOrdinatorDashboard tiles and MyCaller's call-location lookup.
    try:
        cursor.execute("EXEC WebProc_DiscAnswerSummary ?", candidate_id)
    except Exception:
        pass


# -------------------- DISCAnswer --------------------


class DiscCandidateInfo(BaseModel):
    candidate_id: int
    name: str
    mobile_no: str
    email_id: str


class DiscAnswerSummaryRow(BaseModel):
    sr_no: int
    quality: str
    most_count: int
    least_count: int
    most_level_no: int
    least_level_no: int


@router.get("/disc-candidate-info", response_model=DiscCandidateInfo)
def get_disc_candidate_info(current: CurrentCandidate = Depends(get_current_candidate)) -> DiscCandidateInfo:
    with get_cursor() as cursor:
        cursor.execute("Select CandidateID, Name, MobileNo, EmailID From rspl_Candidate Where CandidateID = ?", current.candidate_id)
        rows = rows_to_dicts(cursor)
    r = rows[0] if rows else {}
    return DiscCandidateInfo(
        candidate_id=current.candidate_id, name=r.get("Name") or "", mobile_no=r.get("MobileNo") or "",
        email_id=r.get("EmailID") or "",
    )


@router.get("/disc-answer-summary", response_model=list[DiscAnswerSummaryRow])
def get_disc_answer_summary(current: CurrentCandidate = Depends(get_current_candidate)) -> list[DiscAnswerSummaryRow]:
    with get_cursor() as cursor:
        _refresh_disc_answer_summary(cursor, current.candidate_id)
        cursor.execute(
            "Select SrNo, Quality, MostCount, LeastCount, MostLevelNo, LeastLevelNo "
            "From rspl_DiscAnswerSummary Where graphEnabled = 1 and CandidateID = ?",
            current.candidate_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        DiscAnswerSummaryRow(
            sr_no=r["SrNo"], quality=r["Quality"] or "", most_count=r["MostCount"] or 0,
            least_count=r["LeastCount"] or 0, most_level_no=r["MostLevelNo"] or 0, least_level_no=r["LeastLevelNo"] or 0,
        )
        for r in rows
    ]


# -------------------- QuestionPaperTemplate --------------------


class QuestionPaperTemplateRow(BaseModel):
    question_paper_id: int
    name: str
    department: str
    time_interval: int
    disabled: bool


class AddQuestionPaperTemplateRequest(BaseModel):
    name: str
    department: str
    time_interval: int


@router.get("/question-paper-templates", response_model=list[QuestionPaperTemplateRow])
def get_question_paper_templates() -> list[QuestionPaperTemplateRow]:
    with get_cursor() as cursor:
        cursor.execute("Select * From RSPL_QuestionPaperTemplate Order By QuestionPaperID")
        rows = rows_to_dicts(cursor)
    return [
        QuestionPaperTemplateRow(
            question_paper_id=r["QuestionPaperID"], name=r["Name"] or "", department=r["Department"] or "",
            time_interval=r["TimeInterval"] or 0, disabled=bool(r["Disabled"]),
        )
        for r in rows
    ]


@router.post("/question-paper-templates")
def add_question_paper_template(body: AddQuestionPaperTemplateRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Declare @QuestionPaperID as integer; "
            "Select @QuestionPaperID=ISNULL(MAX(QuestionPaperID),0)+1 From RSPL_QuestionPaperTemplate; "
            "Insert into RSPL_QuestionPaperTemplate(QuestionPaperID,Name,Department,TimeInterval,Disabled) "
            "Values(@QuestionPaperID, ?, ?, ?, 0)",
            body.name, body.department, body.time_interval,
        )
    return {"success": True}


@router.put("/question-paper-templates/{question_paper_id}")
def update_question_paper_template(question_paper_id: int, body: QuestionPaperTemplateRow) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Update RSPL_QuestionPaperTemplate set Name = ?, Department = ?, TimeInterval = ?, Disabled = ? Where QuestionPaperID = ?",
            body.name, body.department, body.time_interval, body.disabled, question_paper_id,
        )
    return {"success": True}


# -------------------- QuestionPaper (authoring) --------------------


class QuestionPaperQuestionRow(BaseModel):
    question_paper_id: int
    question_id: int
    question: str
    question_type: str
    answer_list: str
    actual_answer: str
    disabled: bool


class AddQuestionRequest(BaseModel):
    question: str
    question_type: str
    answer_list: str
    actual_answer: str


@router.get("/question-paper-templates/{question_paper_id}/questions", response_model=list[QuestionPaperQuestionRow])
def get_question_paper_questions(question_paper_id: int) -> list[QuestionPaperQuestionRow]:
    with get_cursor() as cursor:
        cursor.execute("Select * From RSPL_QuestionPaper Where QuestionPaperID = ? Order By SortIndex", question_paper_id)
        rows = rows_to_dicts(cursor)
    return [
        QuestionPaperQuestionRow(
            question_paper_id=r["QuestionPaperID"], question_id=r["QuestionID"], question=r["Question"] or "",
            question_type=r["QuestionType"] or "", answer_list=r["AnswerList"] or "", actual_answer=r["ActualAnswer"] or "",
            disabled=bool(r["Disabled"]),
        )
        for r in rows
    ]


@router.post("/question-paper-templates/{question_paper_id}/questions")
def add_question_paper_question(question_paper_id: int, body: AddQuestionRequest) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "EXEC webproc_AddQuestion ?, ?, ?, ?, ?",
            question_paper_id, body.question, body.question_type, body.answer_list, body.actual_answer,
        )
    return {"success": True}


@router.put("/question-paper-templates/{question_paper_id}/questions/{question_id}")
def update_question_paper_question(question_paper_id: int, question_id: int, body: QuestionPaperQuestionRow) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Update RSPL_QuestionPaper set Question = ?, QuestionType = ?, AnswerList = ?, ActualAnswer = ?, Disabled = ? "
            "Where QuestionPaperID = ? and QuestionID = ?",
            body.question, body.question_type, body.answer_list, body.actual_answer, body.disabled,
            question_paper_id, question_id,
        )
    return {"success": True}


# -------------------- AptiQuestion (candidate exam) --------------------


class ExamQuestion(BaseModel):
    question_id: int
    question_paper_id: int
    question: str
    question_type: str
    answer_options: list[str]


class ExamPaper(BaseModel):
    time_interval_min: int
    questions: list[ExamQuestion]


class ExamAnswerIn(BaseModel):
    question_id: int
    answer_given: str


class SubmitExamAnswersRequest(BaseModel):
    answers: list[ExamAnswerIn]


@router.get("/exam/{question_paper_id}", response_model=ExamPaper)
def get_exam_questions(question_paper_id: int) -> ExamPaper:
    with get_cursor() as cursor:
        cursor.execute("Select TimeInterval From RSPL_QuestionPaperTemplate Where QuestionPaperID = ?", question_paper_id)
        template_row = cursor.fetchone()
        time_interval = template_row[0] if template_row else 0
        cursor.execute(
            "Select * from RSPL_QuestionPaper where QuestionPaperId = ? and Disabled = 0 order by SortIndex",
            question_paper_id,
        )
        rows = rows_to_dicts(cursor)
    questions = [
        ExamQuestion(
            question_id=r["QuestionID"], question_paper_id=r["QuestionPaperID"], question=r["Question"] or "",
            question_type=r["QuestionType"] or "",
            answer_options=(r["AnswerList"] or "").split(",") if r["QuestionType"] != "Text" else [],
        )
        for r in rows
    ]
    return ExamPaper(time_interval_min=time_interval or 0, questions=questions)


@router.post("/exam/{question_paper_id}/submit")
def submit_exam_answers(
    question_paper_id: int, body: SubmitExamAnswersRequest, current: CurrentCandidate = Depends(get_current_candidate),
) -> dict:
    with get_cursor() as cursor:
        for a in body.answers:
            cursor.execute(
                "Insert into RSPL_AnswerSheet(CandidateID,QuestionPaperID,QuestionID,AnswerGiven,AnswerDate,AnswerTime) "
                "Values(?, ?, ?, ?, ?, GetDate())",
                current.candidate_id, question_paper_id, a.question_id, a.answer_given, date.today(),
            )
        cursor.execute("Update RSPL_Candidate Set QuestionPaperID = 0 Where CandidateID = ?", current.candidate_id)
    return {"success": True}


# -------------------- AptiAns / AptiNotification --------------------


class AnswerRegisterRow(BaseModel):
    candidate_id: int
    question_paper_id: int
    name: str
    mobile_no: str
    email_id: str
    department: str
    qualification: str
    exam_date: str | None
    exam_name: str
    marks: int
    out_of: int


class AnswerRegisterDetailRow(BaseModel):
    question_id: int
    question: str
    answer_given: str
    marks: int


class SendBulkSmsRequest(BaseModel):
    candidate_ids: list[int]
    message: str


@router.get("/answer-register", response_model=list[AnswerRegisterRow])
def get_answer_register(batch_id: int) -> list[AnswerRegisterRow]:
    with get_cursor() as cursor:
        cursor.execute("Select * From vw_CandidateAnswerSheet Where BatchID = ?", batch_id)
        rows = rows_to_dicts(cursor)
    return [
        AnswerRegisterRow(
            candidate_id=r["CandidateID"], question_paper_id=r["QuestionPaperID"], name=r["Name"] or "",
            mobile_no=r["MobileNo"] or "", email_id=r["EmailID"] or "", department=r["Department"] or "",
            qualification=r["Qualification"] or "", exam_date=r["ExamDate"].isoformat() if r["ExamDate"] else None,
            exam_name=r["ExamName"] or "", marks=r["Marks"] or 0, out_of=r["OutOf"] or 0,
        )
        for r in rows
    ]


@router.get("/answer-register/{candidate_id}/{question_paper_id}", response_model=list[AnswerRegisterDetailRow])
def get_answer_register_detail(candidate_id: int, question_paper_id: int) -> list[AnswerRegisterDetailRow]:
    with get_cursor() as cursor:
        cursor.execute(
            "Select Q.QuestionID, Q.Question, RAS.AnswerGiven, "
            "(Case When Q.ActualAnswer = RAS.AnswerGiven then 1 else 0 end) as Marks "
            "From RSPL_QuestionPaper Q "
            "Inner join RSPL_AnswerSheet RAS ON RAS.QuestionID = Q.QuestionID and RAS.QuestionPaperID = Q.QuestionPaperID "
            "Inner join RSPL_Candidate c ON c.Candidateid = RAs.CandidateID "
            "Where C.CandidateID = ? and RAS.QuestionPaperID = ?",
            candidate_id, question_paper_id,
        )
        rows = rows_to_dicts(cursor)
    return [
        AnswerRegisterDetailRow(question_id=r["QuestionID"], question=r["Question"] or "", answer_given=r["AnswerGiven"] or "", marks=r["Marks"] or 0)
        for r in rows
    ]


@router.post("/sms/bulk")
def send_bulk_sms(body: SendBulkSmsRequest) -> dict:
    # Mirrors AptiNotification.aspx.vb's btnSend_Click (db.SendSMS per
    # selected row) — stubbed, no SMS gateway wired up in this stack, same
    # established treatment as every other outbound SMS/WhatsApp/email send
    # in this migration (see project_rspl_angular memory).
    return {"success": True}


# -------------------- DISCCandidate / DiscCandidateAdd (admin) --------------------


class DiscCandidateRow(BaseModel):
    candidate_id: int
    name: str
    mobile_no: str
    department: str
    email_id: str
    qualification: str
    adapted_behavior: str
    natural_behavior: str
    question_paper_id: int
    question_paper_name: str


class DiscCandidateDetail(BaseModel):
    candidate_id: int
    name: str
    mobile_no: str
    email_id: str
    department: str
    qualification: str
    login_name: str
    password: str
    enabled: bool
    show_answer: bool
    question_paper_id: int
    batch_id: int


@router.get("/disc-candidates", response_model=list[DiscCandidateRow])
def get_disc_candidates() -> list[DiscCandidateRow]:
    with get_cursor() as cursor:
        cursor.execute("Exec WebProc_GetDiscCandidate 0")
        rows = rows_to_dicts(cursor)
    return [
        DiscCandidateRow(
            candidate_id=r["CandidateID"], name=r["Name"] or "", mobile_no=r["MobileNo"] or "",
            department=r["Department"] or "", email_id=r["EmailID"] or "", qualification=r["Qualification"] or "",
            adapted_behavior=r["AdaptedBehavior"] or "", natural_behavior=r["NaturalBehavior"] or "",
            question_paper_id=r["QuestionPaperID"] or 0, question_paper_name=r["QuestionPaperName"] or "",
        )
        for r in rows
    ]


@router.get("/question-paper-options", response_model=list[LookupOption])
def get_question_paper_options() -> list[LookupOption]:
    with get_cursor() as cursor:
        cursor.execute("Select QuestionPaperID, Name From RSPL_QuestionPaperTemplate Where Disabled = 0")
        return [LookupOption(label=r["Name"] or "", value=r["QuestionPaperID"]) for r in rows_to_dicts(cursor)]


@router.get("/disc-candidates/{candidate_id}", response_model=DiscCandidateDetail)
def get_disc_candidate_detail(candidate_id: int) -> DiscCandidateDetail:
    with get_cursor() as cursor:
        cursor.execute("Exec WebProc_getDiscCandidate ?", candidate_id)
        rows = rows_to_dicts(cursor)
    if not rows:
        return DiscCandidateDetail(
            candidate_id=candidate_id, name="", mobile_no="", email_id="", department="", qualification="",
            login_name="", password="", enabled=False, show_answer=False, question_paper_id=0, batch_id=0,
        )
    r = rows[0]
    return DiscCandidateDetail(
        candidate_id=r["CandidateID"], name=r["Name"] or "", mobile_no=r["MobileNo"] or "",
        email_id=r["EmailID"] or "", department=r["Department"] or "", qualification=r["Qualification"] or "",
        login_name=r["LoginName"] or "",
        password=legacy_password_obfuscate(r["Password"]) if r.get("Password") else "",
        enabled=bool(r["Enabled"]), show_answer=bool(r["ShowAnswer"]), question_paper_id=r["QuestionPaperID"] or 0,
        batch_id=r["BatchID"] or 0,
    )


@router.post("/disc-candidates")
def save_disc_candidate(body: DiscCandidateDetail) -> dict:
    with get_cursor() as cursor:
        cursor.execute(
            "Exec webProc_AddEditCandidate ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', ?, ?",
            body.candidate_id, body.name, body.mobile_no, body.email_id, body.department, body.qualification,
            body.login_name, legacy_password_obfuscate(body.password), body.enabled, body.show_answer,
            body.question_paper_id, body.batch_id,
        )
    return {"success": True}
