import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import io
import os
import json
import uuid
from html import escape
import qrcode
import requests

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
    KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from supabase import create_client


# ==========================================================
# PAGE CONFIG
# ==========================================================

st.set_page_config(
    page_title="TPL QA/QC Fabrication System",
    layout="centered",
    page_icon="⚡"
)


# ==========================================================
# SUPABASE CONFIGURATION
# ==========================================================

try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

except Exception as e:
    st.error(
        "❌ Supabase connection failed.\n\n"
        "Please check SUPABASE_URL and SUPABASE_KEY "
        "inside Streamlit Cloud → Settings → Secrets."
    )
    st.stop()


JOBS_TABLE = "tpl_jobs"
PHOTO_BUCKET = "tpl-photos"

# IMPORTANT: Existing tpl_jobs/job_data records are preserved.
# This update adds a separate notifications table only; it does not
# replace or migrate existing job records.

PUBLIC_DOMAIN = "https://tpl-fabrication-app-evtpzepaiqfnt5gkqh8brx.streamlit.app"


# ==========================================================
# LOGIN CREDENTIALS
# ==========================================================

USERS = {
    # Existing editor account is retained as Admin so the existing
    # login/password continues to work.
    "tpl_e": {
        "password": "E112233",
        "role": "admin"
    },

    "tpl_qc": {
        "password": "QC0000",
        "role": "qc"
    },

    "tpl_v": {
        "password": "V0000",
        "role": "viewer"
    },
}

NOTIFICATIONS_TABLE = "notifications"


# ==========================================================
# TIME
# ==========================================================

def get_sl_time():

    sl_tz = pytz.timezone("Asia/Colombo")

    return datetime.now(sl_tz).strftime(
        "%Y-%m-%d %I:%M %p"
    )


# ==========================================================
# RECORD ID
# ==========================================================

def make_record_id():

    return f"REC-{uuid.uuid4().hex[:12].upper()}"


# ==========================================================
# PHOTO LIST NORMALIZATION
# ==========================================================

def normalize_photo_list(value):

    if not value:
        return []

    if isinstance(value, list):
        return [
            str(x)
            for x in value
            if x
        ]

    return [str(value)]


# ==========================================================
# JOB MIGRATION / DEFAULT VALUES
# ==========================================================

def migrate_job(j):

    if not j.get("record_id"):
        j["record_id"] = make_record_id()

    if (
        "rectifications" not in j
        or not isinstance(j.get("rectifications"), list)
    ):
        j["rectifications"] = []

    if "status" not in j:
        j["status"] = "In Progress"

    if not j.get("start_time"):
        j["start_time"] = get_sl_time()

    for r in j["rectifications"]:

        if "photos" not in r:

            r["photos"] = normalize_photo_list(
                r.get("photo")
            )

        if "fixed_photos" not in r:

            r["fixed_photos"] = normalize_photo_list(
                r.get("fixed_photo")
            )

        r.setdefault(
            "worker_done",
            False
        )

        r.setdefault(
            "action",
            ""
        )

        r.setdefault(
            "time",
            "N/A"
        )

    if "qc_final_approval_photos" not in j:

        j["qc_final_approval_photos"] = normalize_photo_list(
            j.get("qc_final_approval_photo")
        )

    return j


# ==========================================================
# SUPABASE DATABASE
# ==========================================================

def load_data():

    try:

        response = (
            supabase
            .table(JOBS_TABLE)
            .select("record_id,job_data")
            .order(
                "created_at",
                desc=False
            )
            .execute()
        )

        rows = response.data or []

        jobs = []

        for row in rows:

            job = row.get("job_data")

            if job:

                job = migrate_job(job)

                jobs.append(job)

        return jobs

    except Exception as e:

        st.error(
            f"❌ Could not load jobs from Supabase:\n\n{e}"
        )

        return []


# ==========================================================
# SAVE ONE JOB
# ==========================================================

def save_job(job):

    job = migrate_job(job)

    record_id = job["record_id"]

    payload = {
        "record_id": record_id,
        "job_data": job,
        "updated_at": datetime.now(
            pytz.UTC
        ).isoformat()
    }

    try:

        supabase \
            .table(JOBS_TABLE) \
            .upsert(
                payload,
                on_conflict="record_id"
            ) \
            .execute()

        return True

    except Exception as e:

        st.error(
            f"❌ Database save failed:\n\n{e}"
        )

        return False


# ==========================================================
# NOTIFICATIONS
# ==========================================================

def create_notification(recipient_username, title, message, job=None, notification_type="general"):

    payload = {
        "recipient_username": recipient_username,
        "title": title,
        "message": message,
        "job_record_id": (job or {}).get("record_id"),
        "job_id": (job or {}).get("job_id"),
        "notification_type": notification_type,
        "is_read": False,
        "created_at": datetime.now(pytz.UTC).isoformat()
    }

    try:

        supabase.table(NOTIFICATIONS_TABLE).insert(payload).execute()
        return True

    except Exception:

        # Notifications are supplementary. A notification failure must
        # never prevent an existing job from being saved.
        return False


def notify_users(usernames, title, message, job=None, notification_type="general"):

    for username in usernames:
        if username:
            create_notification(
                username,
                title,
                message,
                job,
                notification_type
            )


def get_usernames_by_role(role):

    return [
        username
        for username, info in USERS.items()
        if info.get("role") == role
    ]


def load_notifications(username):

    try:

        response = (
            supabase
            .table(NOTIFICATIONS_TABLE)
            .select("*")
            .eq("recipient_username", username)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        return response.data or []

    except Exception:

        return []


def mark_notification_read(notification_id):

    try:

        supabase.table(NOTIFICATIONS_TABLE).update({
            "is_read": True
        }).eq("id", notification_id).execute()
        return True

    except Exception:

        return False


# ==========================================================
# SAVE ALL JOBS
# ==========================================================

def save_data(data):

    for job in data:

        save_job(job)


# ==========================================================
# DELETE JOB FROM SUPABASE
# ==========================================================

def delete_job_record(record_id):

    try:

        (
            supabase
            .table(JOBS_TABLE)
            .delete()
            .eq(
                "record_id",
                record_id
            )
            .execute()
        )

        return True

    except Exception as e:

        st.error(
            f"❌ Database delete failed:\n\n{e}"
        )

        return False


# ==========================================================
# STORAGE PATH
# ==========================================================

def make_storage_path(record_id, category):

    return (
        f"jobs/"
        f"{record_id}/"
        f"{category}/"
        f"{uuid.uuid4().hex}.jpg"
    )


# ==========================================================
# UPLOAD IMAGE TO SUPABASE STORAGE
# ==========================================================

def upload_photo(uploaded_file, record_id, category):

    try:

        image = Image.open(
            uploaded_file
        )

        # Convert image to RGB
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new(
                "RGB",
                image.size,
                "white"
            )

            if image.mode == "RGBA":

                background.paste(
                    image,
                    mask=image.getchannel("A")
                )

            else:

                background.paste(image)

            image = background

        else:

            image = image.convert("RGB")

        # Resize large phone photos
        max_width = 1600

        if image.width > max_width:

            ratio = (
                max_width /
                float(image.width)
            )

            new_height = int(
                image.height * ratio
            )

            image = image.resize(
                (
                    max_width,
                    new_height
                ),
                Image.LANCZOS
            )

        # Compress image
        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=82,
            optimize=True
        )

        buffer.seek(0)

        storage_path = make_storage_path(
            record_id,
            category
        )

        supabase.storage \
            .from_(PHOTO_BUCKET) \
            .upload(
                path=storage_path,
                file=buffer.getvalue(),
                file_options={
                    "content-type": "image/jpeg",
                    "cache-control": "3600",
                    "upsert": "false"
                }
            )

        public_url = (
            supabase
            .storage
            .from_(PHOTO_BUCKET)
            .get_public_url(
                storage_path
            )
        )

        return public_url

    except Exception as e:

        st.error(
            f"❌ Photo upload failed:\n\n{e}"
        )

        return None


# ==========================================================
# DELETE PHOTO FROM STORAGE
# ==========================================================

def delete_photo_url(photo_url):

    if not photo_url:
        return

    try:

        marker = f"/storage/v1/object/public/{PHOTO_BUCKET}/"

        if marker not in photo_url:
            return

        storage_path = photo_url.split(
            marker,
            1
        )[1]

        (
            supabase
            .storage
            .from_(PHOTO_BUCKET)
            .remove(
                [storage_path]
            )
        )

    except Exception:
        pass


# ==========================================================
# DOWNLOAD PHOTO FOR PDF
# ==========================================================

def get_photo_for_pdf(url):

    try:

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        image_buffer = io.BytesIO(
            response.content
        )

        image = Image.open(
            image_buffer
        )

        image = image.convert("RGB")

        output = io.BytesIO()

        image.save(
            output,
            format="JPEG"
        )

        output.seek(0)

        return output

    except Exception:

        return None


# ==========================================================
# DISPLAY SAVED PHOTOS
# ==========================================================

def photo_columns(
    photo_paths,
    prefix,
    width=150,
    allow_remove=True
):

    valid = [
        p
        for p in photo_paths
        if p
    ]

    if not valid:

        return False

    for i, path in enumerate(valid):

        if allow_remove:

            col1, col2 = st.columns(
                [4, 1]
            )

            with col1:

                st.image(
                    path,
                    width=width,
                    caption=f"Photo {i + 1}"
                )

            with col2:

                st.write("")

                if st.button(
                    "🗑️ Remove",
                    key=(
                        f"{prefix}_remove_{i}_"
                        f"{uuid.uuid5(uuid.NAMESPACE_URL, path)}"
                    )
                ):

                    delete_photo_url(
                        path
                    )

                    photo_paths.remove(
                        path
                    )

                    return True

        else:

            st.image(
                path,
                width=width,
                caption=f"Photo {i + 1}"
            )

    return False


# ==========================================================
# INITIAL DATABASE LOAD
# ==========================================================

current_db = load_data()


# ==========================================================
# SESSION STATE
# ==========================================================

st.session_state.jobs_db = current_db

if "delete_confirm_id" not in st.session_state:

    st.session_state.delete_confirm_id = None


if "photo_uploader_versions" not in st.session_state:

    st.session_state.photo_uploader_versions = {}


def uploader_version(name):

    return st.session_state.photo_uploader_versions.get(
        name,
        0
    )


def bump_uploader_version(name):

    st.session_state.photo_uploader_versions[name] = (
        uploader_version(name) + 1
    )


# ==========================================================
# PUBLIC DIGITAL CERTIFICATE
# ==========================================================

verify_id = st.query_params.get(
    "verify_job"
)


if verify_id:

    clean_id = (
        str(verify_id)
        .strip()
        .lower()
    )

    matched = [
        j
        for j in current_db
        if str(
            j.get("record_id", "")
        ).strip().lower()
        == clean_id
    ]

    if not matched:

        matched = [
            j
            for j in current_db
            if str(
                j.get("job_id", "")
            ).strip().lower()
            == clean_id
        ]

    st.html(
        """
        <style>
        #MainMenu, footer, header, .stDeployButton,
        [data-testid="stToolbar"], [data-testid="stDecoration"] {
            display:none !important;
        }
        .block-container {
            padding-top:1rem !important;
            padding-bottom:2rem !important;
            max-width:680px !important;
        }
        body {
            background-color:#f8fafc;
        }
        </style>
        """
    )

    if matched:

        job = matched[0]

        st.html(
            """
            <div style="background:#003366;color:white;padding:22px 14px;
                        border-radius:12px;text-align:center;">
                <h2 style="margin:0;color:white;letter-spacing:1.2px;
                           font-size:21px;font-weight:800;">
                    TRADE PROMOTERS LIMITED
                </h2>
                <p style="margin:5px 0 0 0;font-size:11px;color:#93c5fd;
                          letter-spacing:.8px;text-transform:uppercase;">
                    GENERATOR FABRICATION QA/QC CLEARANCE CERTIFICATE
                </p>
                <div style="margin-top:12px;">
                    <span style="background:#16a34a;color:white;padding:5px 16px;
                                 border-radius:20px;font-weight:bold;font-size:12px;
                                 display:inline-block;">
                        ✓ QUALITY VERIFIED &amp; COMPLETED
                    </span>
                </div>
            </div>
            """
        )

        st.write("")

        job_id_html = escape(str(job.get("job_id", "N/A")))
        worker_html = escape(str(job.get("worker", "N/A")))
        start_html = escape(str(job.get("start_time", "N/A")))
        completed_html = escape(str(job.get("completed_time", "N/A")))

        st.html(
            f"""
            <div style="background:white;border-radius:10px;padding:14px;
                        border:1px solid #e2e8f0;margin-bottom:15px;">
                <table style="width:100%;font-size:13px;line-height:1.8;">
                    <tr>
                        <td style="color:#64748b;width:45%;">Job ID:</td>
                        <td style="font-weight:bold;color:#0f172a;">{job_id_html}</td>
                    </tr>
                    <tr>
                        <td style="color:#64748b;">Fabrication Lead:</td>
                        <td style="font-weight:bold;color:#0f172a;">{worker_html}</td>
                    </tr>
                    <tr>
                        <td style="color:#64748b;">Started Date/Time:</td>
                        <td>{start_html}</td>
                    </tr>
                    <tr>
                        <td style="color:#64748b;">Completed Date/Time:</td>
                        <td style="color:#16a34a;font-weight:bold;">{completed_html}</td>
                    </tr>
                </table>
            </div>
            """
        )

        st.markdown(
            "##### 📋 Fabrication Scope"
        )

        st.info(
            job.get(
                "desc",
                "N/A"
            )
        )

        st.markdown(
            "##### 🛠️ QC Rectifications & Clearances"
        )

        rects = job.get(
            "rectifications",
            []
        )

        if not rects:

            st.success(
                "Clean pass. Initial inspection passed with standard engineering tolerances."
            )

        else:

            for idx, r in enumerate(rects):

                st.markdown(
                    f"**Defect #{idx+1}:** "
                    f"{r.get('defect','-')}"
                )

                st.markdown(
                    f"✓ **Action Taken:** "
                    f"{r.get('action','-')}"
                )

                defect_photos = normalize_photo_list(
                    r.get(
                        "photos",
                        r.get("photo")
                    )
                )

                fixed_photos = normalize_photo_list(
                    r.get(
                        "fixed_photos",
                        r.get("fixed_photo")
                    )
                )

                if defect_photos:

                    st.markdown(
                        "**QC Defect Photos**"
                    )

                    st.image(
                        defect_photos,
                        use_container_width=True
                    )

                if fixed_photos:

                    st.markdown(
                        "**Worker Fixed Proof Photos**"
                    )

                    st.image(
                        fixed_photos,
                        use_container_width=True
                    )

                st.divider()

        approval_photos = normalize_photo_list(
            job.get(
                "qc_final_approval_photos",
                job.get(
                    "qc_final_approval_photo"
                )
            )
        )

        if approval_photos:

            st.markdown(
                "##### 🔍 QC Final Clearance Sign-Off"
            )

            st.image(
                approval_photos,
                use_container_width=True
            )

        st.html(
            """
            <div style="text-align:center;font-size:11px;color:#64748b;
                        margin-top:25px;border-top:1px solid #cbd5e1;
                        padding-top:12px;">
                Trade Promoters Limited • Generator Installation &amp; QA/QC Division
            </div>
            """
        )

        st.write("---")

        if st.button(
            "⬅️ Back to Workshop Portal"
        ):

            st.query_params.clear()

            st.rerun()

        st.stop()

    else:

        st.error(
            f"Certificate record for Job ID "
            f"'{verify_id}' was not found."
        )

        if st.button(
            "⬅️ Back to Portal"
        ):

            st.query_params.clear()

            st.rerun()

        st.stop()


# ==========================================================
# PDF GENERATOR
# ==========================================================

def create_pdf(
    job,
    qr_link_url
):

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    elements = []

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TStyle",
        parent=styles["Heading1"],
        alignment=1,
        textColor=colors.HexColor(
            "#003366"
        ),
        fontSize=17,
        spaceAfter=3
    )

    sub_style = ParagraphStyle(
        "SStyle",
        parent=styles["Normal"],
        alignment=1,
        textColor=colors.HexColor(
            "#555555"
        ),
        fontSize=9,
        spaceAfter=12
    )

    cell_style = ParagraphStyle(
        "CStyle",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11
    )

    header_cell = ParagraphStyle(
        "HStyle",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        fontName="Helvetica-Bold"
    )

    elements.append(
        Paragraph(
            "TRADE PROMOTERS LIMITED",
            title_style
        )
    )

    elements.append(
        Paragraph(
            "GENERATOR FABRICATION QA/QC CLEARANCE CERTIFICATE",
            sub_style
        )
    )

    job_info = [
        [
            Paragraph(
                f"<b>Job ID:</b> "
                f"{job.get('job_id','')}",
                cell_style
            ),

            Paragraph(
                f"<b>Start Date/Time:</b> "
                f"{job.get('start_time','')}",
                cell_style
            )
        ],

        [
            Paragraph(
                f"<b>Fabrication Lead:</b> "
                f"{job.get('worker','')}",
                cell_style
            ),

            Paragraph(
                f"<b>Completed Time:</b> "
                f"{job.get('completed_time','N/A')}",
                cell_style
            )
        ]
    ]

    t1 = Table(
        job_info,
        colWidths=[270, 270]
    )

    t1.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0,0),
                    (-1,-1),
                    colors.HexColor("#F4F6F8")
                ),

                (
                    "PADDING",
                    (0,0),
                    (-1,-1),
                    5
                ),

                (
                    "GRID",
                    (0,0),
                    (-1,-1),
                    0.5,
                    colors.HexColor("#D2D6DC")
                )
            ]
        )
    )

    elements.append(t1)

    elements.append(
        Spacer(1,8)
    )

    scope_info = [
        [
            Paragraph(
                "<b>Job Scope / Fabrication Details:</b>",
                header_cell
            )
        ],

        [
            Paragraph(
                job.get(
                    "desc",
                    ""
                ),
                cell_style
            )
        ]
    ]

    t_scope = Table(
        scope_info,
        colWidths=[540]
    )

    t_scope.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0,0),
                    (-1,0),
                    colors.HexColor("#E2E8F0")
                ),

                (
                    "PADDING",
                    (0,0),
                    (-1,-1),
                    5
                ),

                (
                    "GRID",
                    (0,0),
                    (-1,-1),
                    0.5,
                    colors.HexColor("#E5E7EB")
                )
            ]
        )
    )

    elements.append(t_scope)

    elements.append(
        Spacer(1,8)
    )

    rects = job.get(
        "rectifications",
        []
    )

    if rects:

        rect_rows = [
            [
                Paragraph(
                    "<b>QC Rectification Audit Log & Photo Proofs:</b>",
                    header_cell
                )
            ]
        ]

        for i, r in enumerate(rects):

            rect_rows.append(
                [
                    Paragraph(
                        f"""
                        <b>Issue #{i+1}:</b>
                        {r.get('defect','')}
                        <br/>
                        <b>Worker Action:</b>
                        {r.get('action','')}
                        -
                        <font color='green'>
                        <b>[Fixed & Cleared]</b>
                        </font>
                        """,
                        cell_style
                    )
                ]
            )

            defect_paths = normalize_photo_list(
                r.get(
                    "photos",
                    r.get("photo")
                )
            )

            fixed_paths = normalize_photo_list(
                r.get(
                    "fixed_photos",
                    r.get("fixed_photo")
                )
            )

            photo_cells = []

            # Defect photo
            if defect_paths:

                defect_buffer = get_photo_for_pdf(
                    defect_paths[0]
                )

                if defect_buffer:

                    photo_cells.append(
                        RLImage(
                            defect_buffer,
                            width=130,
                            height=95
                        )
                    )

                else:

                    photo_cells.append(
                        Paragraph(
                            "Photo unavailable",
                            cell_style
                        )
                    )

            else:

                photo_cells.append(
                    Paragraph(
                        "No Defect Photo",
                        cell_style
                    )
                )

            # Fixed photo
            if fixed_paths:

                fixed_buffer = get_photo_for_pdf(
                    fixed_paths[0]
                )

                if fixed_buffer:

                    photo_cells.append(
                        RLImage(
                            fixed_buffer,
                            width=130,
                            height=95
                        )
                    )

                else:

                    photo_cells.append(
                        Paragraph(
                            "Photo unavailable",
                            cell_style
                        )
                    )

            else:

                photo_cells.append(
                    Paragraph(
                        "No Fixed Photo",
                        cell_style
                    )
                )

            img_table = Table(
                [
                    [
                        Paragraph(
                            "<b>QC Defect Photo:</b>",
                            cell_style
                        ),

                        Paragraph(
                            "<b>Worker Fixed Proof:</b>",
                            cell_style
                        )
                    ],

                    photo_cells
                ],
                colWidths=[
                    260,
                    260
                ]
            )

            img_table.setStyle(
                TableStyle(
                    [
                        (
                            "ALIGN",
                            (0,0),
                            (-1,-1),
                            "CENTER"
                        ),

                        (
                            "PADDING",
                            (0,0),
                            (-1,-1),
                            3
                        )
                    ]
                )
            )

            rect_rows.append(
                [img_table]
            )

        t2 = Table(
            rect_rows,
            colWidths=[540]
        )

        t2.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0,0),
                        (-1,0),
                        colors.HexColor("#FEF3C7")
                    ),

                    (
                        "PADDING",
                        (0,0),
                        (-1,-1),
                        4
                    ),

                    (
                        "GRID",
                        (0,0),
                        (-1,-1),
                        0.5,
                        colors.HexColor("#E5E7EB")
                    )
                ]
            )
        )

        elements.append(t2)

        elements.append(
            Spacer(1,8)
        )

    else:

        pass_table = Table(
            [
                [
                    Paragraph(
                        "<b>QC Inspection Status:</b>",
                        header_cell
                    )
                ],

                [
                    Paragraph(
                        "Clean pass. Inspected and approved to factory standards without defects.",
                        cell_style
                    )
                ]
            ],
            colWidths=[540]
        )

        pass_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0,0),
                        (-1,0),
                        colors.HexColor("#DCFCE7")
                    ),

                    (
                        "PADDING",
                        (0,0),
                        (-1,-1),
                        4
                    ),

                    (
                        "GRID",
                        (0,0),
                        (-1,-1),
                        0.5,
                        colors.HexColor("#E5E7EB")
                    )
                ]
            )
        )

        elements.append(
            pass_table
        )

        elements.append(
            Spacer(1,8)
        )

    # Final approval photos

    approval_paths = normalize_photo_list(
        job.get(
            "qc_final_approval_photos",
            job.get(
                "qc_final_approval_photo"
            )
        )
    )

    if approval_paths:

        try:

            qc_imgs = []

            for p in approval_paths[:4]:

                photo_buffer = get_photo_for_pdf(
                    p
                )

                if photo_buffer:

                    qc_imgs.append(
                        RLImage(
                            photo_buffer,
                            width=140,
                            height=100
                        )
                    )

            if qc_imgs:

                qc_table = Table(
                    [
                        [
                            Paragraph(
                                "<b>QC Final Approval Sign-Off Photos:</b>",
                                header_cell
                            )
                        ],

                        [
                            qc_imgs
                        ]
                    ],
                    colWidths=[540]
                )

                qc_table.setStyle(
                    TableStyle(
                        [
                            (
                                "BACKGROUND",
                                (0,0),
                                (-1,0),
                                colors.HexColor("#DCFCE7")
                            ),

                            (
                                "ALIGN",
                                (0,1),
                                (-1,1),
                                "CENTER"
                            ),

                            (
                                "PADDING",
                                (0,0),
                                (-1,-1),
                                4
                            ),

                            (
                                "GRID",
                                (0,0),
                                (-1,-1),
                                0.5,
                                colors.HexColor("#E5E7EB")
                            )
                        ]
                    )
                )

                elements.append(
                    qc_table
                )

                elements.append(
                    Spacer(1,8)
                )

        except Exception:
            pass

    # QR CODE

    qr = qrcode.QRCode(
        box_size=3,
        border=1
    )

    qr.add_data(
        qr_link_url
    )

    qr.make(
        fit=True
    )

    qr_img = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    qr_buf = io.BytesIO()

    qr_img.save(
        qr_buf,
        format="PNG"
    )

    qr_buf.seek(0)

    sig_info = [
        [
            RLImage(
                qr_buf,
                width=75,
                height=75
            ),

            Paragraph(
                "_________________________<br/><br/>"
                "<b>Workshop Engineer</b>",
                cell_style
            ),

            Paragraph(
                "_________________________<br/><br/>"
                "<b>Quality Engineer</b>",
                cell_style
            )
        ]
    ]

    t3 = Table(
        sig_info,
        colWidths=[
            120,
            210,
            210
        ]
    )

    t3.setStyle(
        TableStyle(
            [
                (
                    "VALIGN",
                    (0,0),
                    (-1,-1),
                    "BOTTOM"
                ),

                (
                    "ALIGN",
                    (0,0),
                    (-1,-1),
                    "CENTER"
                ),

                (
                    "PADDING",
                    (0,0),
                    (-1,-1),
                    2
                )
            ]
        )
    )

    elements.append(
        KeepTogether(t3)
    )

    doc.build(
        elements
    )

    buffer.seek(0)

    return buffer


# ==========================================================
# QR PNG
# ==========================================================

def generate_qr_png(url):

    qr = qrcode.QRCode(
        box_size=5,
        border=2
    )

    qr.add_data(
        str(url).strip()
    )

    qr.make(
        fit=True
    )

    img = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    buf = io.BytesIO()

    img.save(
        buf,
        format="PNG"
    )

    return buf.getvalue()


# ==========================================================
# LOGIN
# ==========================================================

st.title(
    "⚡ TPL Generator Fabrication & QA/QC Portal"
)


if "auth_user" not in st.session_state:

    st.session_state.auth_user = None
    st.session_state.auth_role = None


if not st.session_state.auth_user:

    st.subheader(
        "🔐 Login"
    )

    with st.form(
        "login_form"
    ):

        login_username = st.text_input(
            "Username"
        )

        login_password = st.text_input(
            "Password",
            type="password"
        )

        login_btn = st.form_submit_button(
            "Login"
        )

        if login_btn:

            user = USERS.get(
                login_username.strip()
            )

            if (
                user
                and user["password"]
                == login_password
            ):

                st.session_state.auth_user = (
                    login_username.strip()
                )

                st.session_state.auth_role = (
                    user["role"]
                )

                st.rerun()

            else:

                st.error(
                    "Invalid username or password."
                )

    st.stop()


# ==========================================================
# ROLE
# ==========================================================

current_role = st.session_state.auth_role

is_admin = current_role == "admin"
is_qc = current_role == "qc"
is_viewer = current_role == "viewer"

# Kept as a compatibility flag for existing code paths.
# Only Admin has the old full editor privileges.
is_editor = is_admin


with st.sidebar:

    st.write(
        f"👤 Logged in as "
        f"**{st.session_state.auth_user}**"
    )

    st.caption(
        "Role: "
        f"{st.session_state.auth_role.upper()}"
    )

    if st.button(
        "Logout"
    ):

        st.session_state.auth_user = None
        st.session_state.auth_role = None

        st.rerun()


if is_viewer:

    st.info(
        "👁️ Viewer mode: you can browse jobs and certificates, "
        "but editing is disabled."
    )
elif is_qc:

    st.info(
        "🔍 QC mode: you can log defects and give Final QC Clearance. "
        "Job creation and rectification are restricted to Admin."
    )


# ==========================================================
# NOTIFICATION CENTER
# ==========================================================

notifications = load_notifications(
    st.session_state.auth_user
)

unread_count = sum(
    1
    for n in notifications
    if not n.get("is_read", False)
)

with st.sidebar:

    st.markdown("### 🔔 Notifications")

    if unread_count:
        st.warning(f"{unread_count} unread notification(s)")
    else:
        st.caption("No unread notifications")

    if notifications:
        for n_idx, note in enumerate(notifications[:10]):

            icon = "🔵" if not note.get("is_read", False) else "⚪"

            st.markdown(
                f"{icon} **{note.get('title', 'Notification')}**"
            )
            st.caption(
                f"{note.get('message', '')}\n"
                f"{note.get('created_at', '')}"
            )

            if not note.get("is_read", False):
                if st.button(
                    "Mark as read",
                    key=f"read_note_{note.get('id', n_idx)}"
                ):
                    mark_notification_read(note.get("id"))
                    st.rerun()

            st.divider()


# ==========================================================
# 1. ASSIGN NEW JOB
# ==========================================================

if is_editor:

    st.subheader(
        "1. Assign New Fabrication Job"
    )

    with st.form(
        "assign_job_form",
        clear_on_submit=True
    ):

        new_job_id = st.text_input(
            "Job ID",
            placeholder="e.g. TPL-GEN-002"
        )

        new_desc = st.text_area(
            "Job Scope / Fabrication Details",
            placeholder=(
                "Specify canopy dimensions, "
                "steel grade, welding specs..."
            )
        )

        new_worker = st.text_input(
            "Assigned Employee Name"
        )

        st.caption(
            "🕒 Start Date/Time will be recorded "
            "automatically when you click "
            "**Assign Job** (Sri Lanka time)."
        )

        assign_btn = st.form_submit_button(
            "Assign Job"
        )

        if (
            assign_btn
            and new_job_id
            and new_desc
            and new_worker
        ):

            existing_ids = [
                j.get(
                    "job_id",
                    ""
                ).strip().lower()
                for j in st.session_state.jobs_db
            ]

            if (
                new_job_id
                .strip()
                .lower()
                in existing_ids
            ):

                st.error(
                    f"Job ID '{new_job_id}' already exists. "
                    "Please use a unique Job ID."
                )

            else:

                new_job = {
                    "record_id": make_record_id(),

                    "job_id": new_job_id.strip(),

                    "desc": new_desc.strip(),

                    "worker": new_worker.strip(),

                    "start_time": get_sl_time(),

                    "status": "In Progress",

                    "rectifications": [],

                    "qc_final_approval_photos": []
                }

                if save_job(new_job):

                    st.session_state.jobs_db.append(
                        new_job
                    )

                    notify_users(
                        get_usernames_by_role("qc")
                        + get_usernames_by_role("viewer"),
                        "New Job Assigned",
                        f"New job {new_job.get('job_id')} has been assigned for fabrication/QC review.",
                        new_job,
                        "new_job"
                    )

                    st.success(
                        f"Job {new_job_id} "
                        "assigned successfully!"
                    )

                    st.rerun()

    st.write("---")


# ==========================================================
# 2. ONGOING FABRICATION
# ==========================================================

st.subheader(
    "2. Ongoing Fabrication & QC Inspection Pipeline"
)


active_jobs = [
    j
    for j in st.session_state.jobs_db
    if j.get("status")
    != "Completed"
]


if not active_jobs:

    st.info(
        "No active jobs currently in progress."
    )

else:

    for a_idx, job in enumerate(active_jobs):

        migrate_job(job)

        record_id = job["record_id"]

        rects = job.get(
            "rectifications",
            []
        )

        all_worker_fixed = (
            len(rects) == 0
            or all(
                r.get(
                    "worker_done",
                    False
                )
                for r in rects
            )
        )

        approval_paths = normalize_photo_list(
            job.get(
                "qc_final_approval_photos",
                []
            )
        )

        qc_photo_uploaded = (
            len(approval_paths) > 0
        )

        status_icon = (
            "🟢"
            if (
                all_worker_fixed
                and qc_photo_uploaded
            )
            else
            (
                "🔴"
                if rects
                and not all_worker_fixed
                else "🟡"
            )
        )

        with st.expander(
            f"{status_icon} "
            f"{job.get('job_id','')} - "
            f"{job.get('worker','')} "
            f"[{job.get('status','In Progress')}]"
        ):

            st.write(
                f"**Description:** "
                f"{job.get('desc','')}"
            )

            st.caption(
                f"Started: "
                f"{job.get('start_time','')}"
            )


            # ==================================================
            # RECTIFICATIONS
            # ==================================================

            if rects:

                st.markdown(
                    "#### ⚠️ QC Rectification Issues:"
                )

                for idx, r in enumerate(rects):

                    st.markdown(
                        f"**Defect #{idx+1}:** "
                        f"{r.get('defect','')} "
                        f"*("
                        f"Logged: {r.get('time','N/A')}"
                        f")*"
                    )

                    defect_paths = normalize_photo_list(
                        r.get(
                            "photos",
                            r.get("photo")
                        )
                    )

                    if defect_paths:

                        st.markdown(
                            "**QC Defect Photos**"
                        )

                        changed = photo_columns(
                            defect_paths,
                            f"defect_{record_id}_{idx}",
                            width=170,
                            allow_remove=is_editor
                        )

                        if changed:

                            r["photos"] = defect_paths

                            save_job(job)

                            st.rerun()


                    col_w1, col_w2 = st.columns(
                        2
                    )


                    # ==========================================
                    # WORKER ACTION
                    # ==========================================

                    with col_w1:

                        if is_editor:

                            previous_worker_done = r.get(
                                "worker_done",
                                False
                            )

                            w_tick = st.checkbox(
                                f"Worker: Completed / Fixed #{idx+1}",

                                value=previous_worker_done,

                                key=(
                                    f"w_chk_"
                                    f"{record_id}_"
                                    f"{idx}"
                                )
                            )

                            action_txt = st.text_input(
                                f"Action Taken #{idx+1}",

                                value=r.get(
                                    "action",
                                    ""
                                ),

                                key=(
                                    f"act_"
                                    f"{record_id}_"
                                    f"{idx}"
                                ),

                                placeholder=(
                                    "e.g. Re-welded "
                                    "and ground smooth"
                                )
                            )

                            r["worker_done"] = w_tick
                            r["action"] = action_txt

                            # Save changes
                            if save_job(job):

                                if (
                                    is_admin
                                    and w_tick
                                    and not previous_worker_done
                                ):

                                    notify_users(
                                        get_usernames_by_role("qc"),
                                        "Rectification Completed by Admin",
                                        f"A rectification item was marked fixed for job {job.get('job_id')}.",
                                        job,
                                        "rectification_completed"
                                    )

                        else:

                            st.write(
                                "Worker Fixed: "
                                f"{'✅ Yes' if r.get('worker_done') else '❌ No'}"
                            )

                            if r.get("action"):

                                st.write(
                                    "Action Taken: "
                                    f"{r.get('action')}"
                                )


                    # ==========================================
                    # FIXED PHOTOS
                    # ==========================================

                    with col_w2:

                        fixed_paths = normalize_photo_list(
                            r.get(
                                "fixed_photos",
                                r.get("fixed_photo")
                            )
                        )

                        if fixed_paths:

                            st.markdown(
                                "**Worker Fixed Proof Photos**"
                            )

                            changed = photo_columns(
                                fixed_paths,
                                f"fixed_{record_id}_{idx}",
                                width=130,
                                allow_remove=is_editor
                            )

                            if changed:

                                r["fixed_photos"] = fixed_paths

                                save_job(job)

                                st.rerun()


                        if is_editor:

                            fixed_uploader_id = (
                                f"fixed_img_"
                                f"{record_id}_"
                                f"{idx}"
                            )

                            new_fixed = st.file_uploader(

                                f"Upload Fixed Photo(s) "
                                f"#{idx+1} (Optional)",

                                type=[
                                    "jpg",
                                    "jpeg",
                                    "png"
                                ],

                                accept_multiple_files=True,

                                key=(
                                    f"{fixed_uploader_id}_v"
                                    f"{uploader_version(fixed_uploader_id)}"
                                )
                            )

                            if new_fixed:

                                added_count = 0

                                for uploaded in new_fixed:

                                    path = upload_photo(
                                        uploaded,
                                        record_id,
                                        f"fixed_{idx+1}"
                                    )

                                    if path:

                                        fixed_paths.append(
                                            path
                                        )

                                        added_count += 1

                                r["fixed_photos"] = (
                                    fixed_paths
                                )

                                if save_job(job):

                                    notify_users(
                                        get_usernames_by_role("qc"),
                                        "Rectification Updated by Admin",
                                        f"Rectification work/photo evidence was updated for job {job.get('job_id')}.",
                                        job,
                                        "rectification_updated"
                                    )

                                bump_uploader_version(
                                    fixed_uploader_id
                                )

                                st.success(
                                    f"{added_count} fixed "
                                    "photo(s) attached "
                                    "successfully."
                                )

                                st.rerun()

                    st.divider()


            else:

                st.success(
                    "No defects logged yet. "
                    "Work progressing normally."
                )


            # ==================================================
            # QC DEFECT FORM
            # Admin and QC can both log defects.
            # ==================================================

            if is_admin or is_qc:

                st.markdown(
                    "#### 🔍 Defect / QC Issue: "
                    "Log Comment / Defect for this Job"
                )

                with st.form(
                    f"qc_add_defect_{record_id}",
                    clear_on_submit=True
                ):

                    defect_text = st.text_area(
                        "Defect / Rectification Note",

                        placeholder=(
                            "Describe issue: "
                            "weld gap, misaligned holes, "
                            "paint run..."
                        ),

                        key=f"def_txt_{record_id}"
                    )

                    photo_files = st.file_uploader(

                        "Upload Inspection / "
                        "Defect Photo(s) (Optional)",

                        type=[
                            "jpg",
                            "jpeg",
                            "png"
                        ],

                        accept_multiple_files=True,

                        key=f"def_img_{record_id}"
                    )

                    st.caption(
                        "You can select multiple photos "
                        "at once. Photos are saved "
                        "directly to Supabase Storage "
                        "after submitting this issue."
                    )

                    add_defect_btn = (
                        st.form_submit_button(
                            "➕ Submit Rectification Issue"
                        )
                    )

                    if (
                        add_defect_btn
                        and defect_text
                    ):

                        photo_paths = []

                        for uploaded in (
                            photo_files or []
                        ):

                            path = upload_photo(
                                uploaded,
                                record_id,
                                f"defect_{len(rects)+1}"
                            )

                            if path:

                                photo_paths.append(
                                    path
                                )

                        job["rectifications"].append(
                            {
                                "defect":
                                    defect_text.strip(),

                                "photos":
                                    photo_paths,

                                "fixed_photos":
                                    [],

                                "worker_done":
                                    False,

                                "action":
                                    "",

                                "time":
                                    get_sl_time()
                            }
                        )

                        job["status"] = (
                            "Needs Rectification"
                        )

                        if save_job(job):

                            if is_admin:
                                recipients = (
                                    get_usernames_by_role("qc")
                                    + get_usernames_by_role("viewer")
                                )
                                title = "New Defect Added by Admin"
                            else:
                                recipients = (
                                    get_usernames_by_role("admin")
                                    + get_usernames_by_role("viewer")
                                )
                                title = "New Defect Added by QC"

                            notify_users(
                                recipients,
                                title,
                                f"A defect was logged for job {job.get('job_id')}.",
                                job,
                                "defect_added"
                            )

                            st.success(
                                f"Rectification issue "
                                f"added to "
                                f"{job.get('job_id')}!"
                            )

                            st.rerun()


            # ==================================================
            # FINAL QC
            # ==================================================

            st.write("---")

            st.markdown(
                "#### 🛡️ QC Final Clearance Photo(s) "
                "(Required for Job Completion)"
            )

            approval_paths = normalize_photo_list(
                job.get(
                    "qc_final_approval_photos",
                    job.get(
                        "qc_final_approval_photo"
                    )
                )
            )

            if approval_paths:

                changed = photo_columns(
                    approval_paths,
                    f"approval_{record_id}",
                    width=180,
                    allow_remove=is_editor
                )

                if changed:

                    job[
                        "qc_final_approval_photos"
                    ] = approval_paths

                    save_job(job)

                    st.rerun()


            if is_qc:

                approval_uploader_id = (
                    f"qc_appr_{record_id}"
                )

                qc_appr_files = st.file_uploader(

                    f"📸 Upload QC Approval "
                    f"Photo(s) / Sign "
                    f"({job.get('job_id')})",

                    type=[
                        "jpg",
                        "jpeg",
                        "png"
                    ],

                    accept_multiple_files=True,

                    key=(
                        f"{approval_uploader_id}_v"
                        f"{uploader_version(approval_uploader_id)}"
                    )
                )

                if qc_appr_files:

                    added_count = 0

                    for uploaded in qc_appr_files:

                        path = upload_photo(
                            uploaded,
                            record_id,
                            "qc_approval"
                        )

                        if path:

                            approval_paths.append(
                                path
                            )

                            added_count += 1

                    job[
                        "qc_final_approval_photos"
                    ] = approval_paths

                    if save_job(job):

                        notify_users(
                            get_usernames_by_role("admin"),
                            "Final QC Clearance Submitted",
                            f"Final QC clearance photo(s) were submitted for job {job.get('job_id')}.",
                            job,
                            "final_qc_clearance"
                        )

                    bump_uploader_version(
                        approval_uploader_id
                    )

                    st.success(
                        f"{added_count} QC approval "
                        "photo(s) attached successfully."
                    )

                    st.rerun()


            if approval_paths:

                st.caption(
                    f"{len(approval_paths)} "
                    "QC approval photo(s) saved."
                )


            # ==================================================
            # COMPLETION
            # ==================================================

            if is_editor:

                st.write("---")

                can_complete = (
                    all_worker_fixed
                    and len(approval_paths) > 0
                )

                if not can_complete:

                    missing_items = []

                    if not all_worker_fixed:

                        missing_items.append(
                            "Worker must mark all "
                            "defects as Fixed"
                        )

                    if not approval_paths:

                        missing_items.append(
                            "QC Approval Photo "
                            "must be uploaded"
                        )

                    st.warning(
                        "🔒 Completion Locked: "
                        + ", and ".join(
                            missing_items
                        )
                    )

                    st.checkbox(
                        f"✅ Mark Job as COMPLETED "
                        f"({job.get('job_id')})",

                        disabled=True,

                        key=(
                            f"dis_comp_"
                            f"{record_id}"
                        )
                    )

                    st.button(
                        f"Submit Final Job "
                        f"({job.get('job_id')})",

                        disabled=True,

                        key=(
                            f"dis_btn_"
                            f"{record_id}"
                        )
                    )

                else:

                    is_comp = st.checkbox(
                        f"✅ Mark Job as COMPLETED "
                        f"({job.get('job_id')})",

                        key=(
                            f"comp_"
                            f"{record_id}"
                        )
                    )

                    if st.button(
                        f"Submit Final Job "
                        f"({job.get('job_id')})",

                        key=(
                            f"sub_"
                            f"{record_id}"
                        )
                    ):

                        if is_comp:

                            job["status"] = (
                                "Completed"
                            )

                            job[
                                "completed_time"
                            ] = get_sl_time()

                            if save_job(job):

                                notify_users(
                                    get_usernames_by_role("admin")
                                    + get_usernames_by_role("qc")
                                    + get_usernames_by_role("viewer"),
                                    "Job Completed",
                                    f"Job {job.get('job_id')} has been completed and is available in the Completed Jobs Archive.",
                                    job,
                                    "job_completed"
                                )

                                st.success(
                                    f"Job "
                                    f"{job.get('job_id')} "
                                    "COMPLETED successfully!"
                                )

                                st.rerun()

                        else:

                            st.warning(
                                "Please tick the "
                                "completion checkbox "
                                "above before submitting."
                            )


                # ==================================================
                # DELETE ACTIVE JOB
                # ==================================================

                st.write("---")

                act_del_key = (
                    f"active_{record_id}"
                )

                if (
                    st.session_state.delete_confirm_id
                    == act_del_key
                ):

                    st.error(
                        f"⚠️ Are you sure you want "
                        f"to delete ongoing job "
                        f"**{job.get('job_id')}**?"
                    )

                    conf_c1, conf_c2 = st.columns(
                        2
                    )

                    with conf_c1:

                        if st.button(
                            "Yes, Delete Job",
                            key=f"act_yes_{record_id}"
                        ):

                            # Delete photos
                            for r in job.get(
                                "rectifications",
                                []
                            ):

                                for p in (
                                    normalize_photo_list(
                                        r.get("photos", [])
                                    )
                                    +
                                    normalize_photo_list(
                                        r.get(
                                            "fixed_photos",
                                            []
                                        )
                                    )
                                ):

                                    delete_photo_url(p)


                            for p in normalize_photo_list(
                                job.get(
                                    "qc_final_approval_photos",
                                    []
                                )
                            ):

                                delete_photo_url(p)


                            if delete_job_record(
                                record_id
                            ):

                                st.session_state.jobs_db = [
                                    j
                                    for j
                                    in st.session_state.jobs_db
                                    if j.get(
                                        "record_id"
                                    )
                                    != record_id
                                ]

                                st.session_state.delete_confirm_id = (
                                    None
                                )

                                st.success(
                                    f"Job "
                                    f"{job.get('job_id')} "
                                    "permanently deleted!"
                                )

                                st.rerun()


                    with conf_c2:

                        if st.button(
                            "No, Cancel",
                            key=f"act_no_{record_id}"
                        ):

                            st.session_state.delete_confirm_id = (
                                None
                            )

                            st.rerun()

                else:

                    if st.button(
                        f"🗑️ Delete This Job "
                        f"({job.get('job_id')})",

                        key=(
                            f"act_del_btn_"
                            f"{record_id}"
                        )
                    ):

                        st.session_state.delete_confirm_id = (
                            act_del_key
                        )

                        st.rerun()


# ==========================================================
# 3. COMPLETED JOBS
# All roles can view/download QR and certificate documents.
# ==========================================================

st.write("---")

st.subheader(
    "3. Completed Jobs Archive"
)


completed_jobs = [
    j
    for j in st.session_state.jobs_db
    if j.get("status")
    == "Completed"
]


if not completed_jobs:

    st.caption(
        "No completed jobs yet."
    )

else:

    for c_idx, c_job in enumerate(
        completed_jobs
    ):

        migrate_job(c_job)

        record_id = c_job[
            "record_id"
        ]

        c_rects = c_job.get(
            "rectifications",
            []
        )

        with st.expander(
            f"🟢 "
            f"{c_job.get('job_id','')} - "
            f"{c_job.get('worker','')} "
            f"(COMPLETED)"
        ):

            st.write(
                f"**Description:** "
                f"{c_job.get('desc','')}"
            )

            st.write(
                f"**Completed At:** "
                f"{c_job.get('completed_time','N/A')}"
            )

            st.write(
                f"**Rectifications Cleared:** "
                f"{len(c_rects)} items."
            )


            # ==================================================
            # QR
            # ==================================================

            direct_qr_url = (
                f"{PUBLIC_DOMAIN}"
                f"/?verify_job={record_id}"
            )

            st.write("---")

            st.markdown(
                "#### 📱 Digital Certificate QR Code:"
            )

            qr_bytes = generate_qr_png(
                direct_qr_url
            )

            qr_col1, qr_col2 = st.columns(
                [1,2]
            )

            with qr_col1:

                st.image(
                    qr_bytes,
                    width=150,
                    caption=(
                        f"Scan to Verify "
                        f"{c_job.get('job_id')}"
                    )
                )

            with qr_col2:

                st.info(
                    "💡 Scan with any smartphone "
                    "camera to open the "
                    "digital certificate."
                )

                st.download_button(

                    label=(
                        "📥 Download QR Code "
                        "(PNG)"
                    ),

                    data=qr_bytes,

                    file_name=(
                        f"TPL_"
                        f"{c_job.get('job_id')}"
                        f"_QR.png"
                    ),

                    mime="image/png",

                    key=(
                        f"qr_dl_"
                        f"{record_id}_"
                        f"{c_idx}"
                    )
                )


            # ==================================================
            # CERTIFICATE BUTTONS
            # ==================================================

            st.write("---")

            btn_col1, btn_col2, btn_col3 = st.columns(
                [1.2, 1.5, 1]
            )


            with btn_col1:

                if st.button(
                    "👁️ View Certificate",
                    key=(
                        f"view_"
                        f"{record_id}_"
                        f"{c_idx}"
                    )
                ):

                    st.query_params[
                        "verify_job"
                    ] = record_id

                    st.rerun()


            with btn_col2:

                pdf_bytes = create_pdf(
                    c_job,
                    direct_qr_url
                )

                st.download_button(

                    label=(
                        "📄 Print PDF Certificate"
                    ),

                    data=pdf_bytes,

                    file_name=(
                        f"TPL_"
                        f"{c_job.get('job_id')}"
                        f"_Certificate.pdf"
                    ),

                    mime="application/pdf",

                    key=(
                        f"dl_"
                        f"{record_id}_"
                        f"{c_idx}"
                    )
                )


            with btn_col3:

                if is_editor:

                    comp_del_key = (
                        f"completed_"
                        f"{record_id}"
                    )

                    if st.button(
                        "🗑️ Delete",
                        key=(
                            f"del_btn_"
                            f"{record_id}_"
                            f"{c_idx}"
                        )
                    ):

                        st.session_state.delete_confirm_id = (
                            comp_del_key
                        )

                        st.rerun()


            # ==================================================
            # DELETE COMPLETED
            # ==================================================

            if (
                is_editor
                and
                st.session_state.delete_confirm_id
                == f"completed_{record_id}"
            ):

                st.write("")

                st.error(
                    f"⚠️ Are you sure you want "
                    f"to delete completed record "
                    f"**{c_job.get('job_id')}**?"
                )

                conf_c1, conf_c2 = st.columns(
                    2
                )

                with conf_c1:

                    if st.button(
                        "Yes, Delete Record",
                        key=f"comp_yes_{record_id}"
                    ):

                        for r in c_job.get(
                            "rectifications",
                            []
                        ):

                            for p in (
                                normalize_photo_list(
                                    r.get(
                                        "photos",
                                        []
                                    )
                                )
                                +
                                normalize_photo_list(
                                    r.get(
                                        "fixed_photos",
                                        []
                                    )
                                )
                            ):

                                delete_photo_url(p)


                        for p in normalize_photo_list(
                            c_job.get(
                                "qc_final_approval_photos",
                                []
                            )
                        ):

                            delete_photo_url(p)


                        if delete_job_record(
                            record_id
                        ):

                            st.session_state.jobs_db = [
                                j
                                for j
                                in st.session_state.jobs_db
                                if j.get(
                                    "record_id"
                                )
                                != record_id
                            ]

                            st.session_state.delete_confirm_id = (
                                None
                            )

                            st.success(
                                f"Job "
                                f"{c_job.get('job_id')} "
                                "permanently deleted!"
                            )

                            st.rerun()


                with conf_c2:

                    if st.button(
                        "No, Cancel",
                        key=f"comp_no_{record_id}"
                    ):

                        st.session_state.delete_confirm_id = (
                            None
                        )

                        st.rerun()
