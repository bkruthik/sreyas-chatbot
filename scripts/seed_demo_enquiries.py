"""
scripts/seed_demo_enquiries.py
================================================================================
Demo enquiry seed script for Sreyas Institute of Engineering and Technology (SIET)
Chatbot.

This script populates the enquiries Excel sheet (`data/enquiries.xlsx`) with ~150
realistic demo enquiries while preserving all existing real enquiries.

Features:
- Idempotent: Skips execution if demo records (@demo.sreyas.ac.in) already exist.
- Header fix: Ensures columns 'Parent Email' and 'Priority' exist in header row.
- Realistic Indian names, phone numbers (9000-series), and diverse questions.
- Specified category and status distributions across the last 90 days.
- Summary report printed upon completion.
================================================================================
"""

import os
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Font

# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCEL_PATH = os.path.join(PROJECT_ROOT, "data", "enquiries.xlsx")
SHEET_NAME = "Enquiries"

DEMO_DOMAIN = "demo.sreyas.ac.in"

COLUMNS = [
    "Enquiry ID",
    "Date",
    "Time",
    "Name",
    "Phone",
    "Email",
    "Mode",
    "Category",
    "Confidence",
    "Question",
    "Status",
    "Assigned To",
    "Remarks",
    "Parent Email",
    "Priority",
]

_ENQUIRY_ID_PATTERN = re.compile(r"^ENQ(\d+)$")

# Target status distribution (Total: 150)
STATUS_DISTRIBUTION = {
    "Pending": 55,
    "In Progress": 20,
    "Reviewed": 35,
    "Closed": 40,
}

# Target category distribution (Total: 150)
CATEGORY_DISTRIBUTION = {
    "ADMISSIONS": 25,
    "FEE STRUCTURE": 20,
    "COURSES AND BRANCHES": 15,
    "EXAMINATION": 15,
    "PLACEMENTS": 20,
    "SCHOLARSHIPS": 10,
    "TRANSPORT": 10,
    "FACILITIES": 10,
    "COLLEGE TIMINGS": 5,
    "GENERAL": 10,
    "PARENT ENQUIRY": 10,
}

# Staff assignments and remarks per status
ASSIGNED_STAFF = {
    "Pending": [""],
    "In Progress": [
        "Admissions Desk",
        "Academic Counselor - Ramesh",
        "Admin Staff - Priya",
        "Transport Coordinator",
        "Placement Coordinator",
    ],
    "Reviewed": [
        "Admissions Officer - Dr. Rao",
        "Accounts Section - Murthy",
        "HOD CSE Office",
        "Exam Branch Coordinator",
    ],
    "Closed": [
        "Helpdesk Staff",
        "Student Welfare Desk",
        "Accounts Desk",
        "Senior Counselor",
    ],
}

STATUS_REMARKS = {
    "Pending": [""],
    "In Progress": [
        "Contacted student, awaiting certificate verification.",
        "Query under review by the admissions committee.",
        "Follow-up call scheduled for tomorrow morning.",
        "Shared course brochure, waiting for branch confirmation.",
        "Sent fee structure breakdown via email.",
    ],
    "Reviewed": [
        "Verified eligibility criteria; student informed over phone.",
        "Fee installment schedule approved and sent to parent.",
        "Exam timetable and revaluation guidelines explained.",
        "Parent counseled regarding branch preferences and cutoff.",
        "Information verified and emailed to applicant.",
    ],
    "Closed": [
        "Admission confirmed under convenor quota.",
        "Fee query fully addressed and receipt acknowledged.",
        "Transport route and bus pass issued.",
        "Placement statistics and recruitment policy explained.",
        "Query successfully resolved.",
    ],
}

# Sample pool of Indian names
FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Karthik", "Sneha", "Sai Teja",
    "Vikram", "Pooja", "Aditya", "Divya", "Nikhil", "Harish", "Kavya",
    "Rahul", "Meera", "Varun", "Anusha", "Deepak", "Shreya", "Manoj",
    "Bhavana", "Suresh", "Swathi", "Rajesh", "Lavanya", "Srikanth",
    "Pranathi", "Akhil", "Keerthi", "Tarun", "Sandhya", "Manish",
    "Harika", "Gautam", "Aishwarya", "Vishal", "Tanvi", "Siddharth",
    "Neharika", "Ramesh", "Deepika", "Kishore", "Sowmya", "Santosh"
]

LAST_NAMES = [
    "Reddy", "Sharma", "Rao", "Kumar", "Iyer", "Verma", "Nair", "Kulkarni",
    "Murthy", "Joshi", "Varma", "Chary", "Patel", "Goud", "Prasad",
    "Nambiar", "Gupta", "Bhat", "Deshmukh", "Menon", "Chowdary", "Naidu"
]

# Realistic enquiry questions by category (8-10+ per category)
CATEGORY_QUESTIONS: Dict[str, List[str]] = {
    "ADMISSIONS": [
        "What is the TG EAPCET cutoff rank for CSE and CSE-Data Science for the current academic year?",
        "How can I apply for B.Tech admission under Management Quota (Category B)?",
        "Is lateral entry admission available for Diploma candidates in ECE and Mech?",
        "What are the mandatory documents needed during college reporting for EAPCET counselling?",
        "When does the Phase 2 reporting deadline close for Sreyas engineering admissions?",
        "Can I get CSE or CSM branch with a 24,000 rank in TS EAMCET under BC-B category?",
        "Are NRI quota seats available in Sreyas, and what is the application procedure?",
        "Do you accept JEE Main scores for direct B.Tech admissions in Computer Science?",
        "What is the procedure for cancellation of admission and refund of tuition fee?",
        "Are admissions open for M.Tech Embedded Systems and VLSI courses this semester?",
    ],
    "FEE STRUCTURE": [
        "What is the annual tuition fee for B.Tech CSE for the 2024-25 batch?",
        "Can the tuition fee and university fee be paid in two equal installments?",
        "Are there any extra laboratory, library, or exam branch fees beyond the convenor fee?",
        "What is the payment procedure for online RTGS/NEFT tuition fee deposit?",
        "Is there a hostel facility inside the campus, and what is the annual hostel fee?",
        "What is the caution deposit amount, and is it refundable upon graduation?",
        "Does the college provide any fee concession for students with top 5,000 EAMCET ranks?",
        "What is the total fee payable for lateral entry students in second year?",
        "How can I get the fee structure certificate for applying for an SBI education loan?",
    ],
    "COURSES AND BRANCHES": [
        "What are the emerging branches offered in Sreyas under CSE (AIML, Data Science, Cyber Security)?",
        "Can a student change their branch at the end of the first year based on CGPA?",
        "What is the total intake for CSE, ECE, and Mechanical Engineering departments?",
        "Is Artificial Intelligence and Data Science (AIDS) branch NBA accredited at Sreyas?",
        "What programming languages and tools are covered in the CSE curriculum syllabus?",
        "Does the college offer B.Tech Honours or Minor degrees in additional specializations?",
        "How do the curriculum and lab infrastructure compare between CSE core and CSE-AIML?",
        "What are the major core electives available in the 3rd and 4th years of ECE?",
        "Are there industry-sponsored labs like AWS, Google, or Cisco on the campus?",
    ],
    "EXAMINATION": [
        "When will the 1st semester mid-term examination timetable be released?",
        "What is the process and fee for applying for JNTUH semester answer script revaluation?",
        "What is the minimum attendance percentage required to receive the semester hall ticket?",
        "How can I apply for a duplicate semester grade sheet / memo from the exam branch?",
        "When will the supplementary examination results for the 2nd year 2nd semester be announced?",
        "Can students with attendance between 65% and 75% apply for condonation on medical grounds?",
        "Where can I find the academic calendar and end-semester practical exam schedules?",
        "What is the procedure for obtaining a Consolidated Marks Memo (CMM) and Provisional Certificate?",
        "How do I clear exam branch fee dues before the examination hall ticket issuance?",
    ],
    "PLACEMENTS": [
        "What is the highest and average CTC offered during recent campus placement drives?",
        "Which top tier companies visited Sreyas for on-campus hiring in the last academic year?",
        "What is the eligibility criteria (backlog count and CGPA) to register for campus placements?",
        "Does the college provide dedicated campus placement training for aptitude, coding, and soft skills?",
        "How many students were placed in TCS, Cognizant, Wipro, and Accenture last year?",
        "Are internship opportunities offered with stipends during the 6th and 7th semesters?",
        "Does the Training and Placement Cell assist with off-campus recruitment opportunities?",
        "What percentage of students in ECE and Mechanical branches secure core engineering jobs?",
        "Are Japanese language training or overseas placement programs organized by SIET?",
    ],
    "SCHOLARSHIPS": [
        "How do I apply for Telangana State ePASS fee reimbursement and scholarship?",
        "Is full fee reimbursement eligible for students with rank below 10,000 in EAMCET?",
        "What certificates are required from the college to submit the national scholarship portal (NSP) form?",
        "Does the college or management provide merit-cum-means scholarships for economically weaker students?",
        "When will the college bio-metric verification for ePASS scholarship renewal be conducted?",
        "Can students admitted under Category-B Management quota claim Telangana ePASS reimbursement?",
        "What is the procedure to obtain a bonafide and study certificate for private NGO scholarships?",
        "Who is the scholarship nodal officer on campus for resolving ePASS application rejections?",
    ],
    "TRANSPORT": [
        "What are the college bus routes and pickup points from Dilsukhnagar, LB Nagar, and Uppal?",
        "What is the annual bus transport fee for students commuting from Secunderabad and ECIL?",
        "Are college buses available in the evening after 4:40 PM for sports and lab practice?",
        "How can I apply for a student bus pass from TSRTC through the college administration?",
        "Is parking space available inside campus for student two-wheelers and four-wheelers?",
        "Can bus transport fees be paid on a semester basis or only as a single annual payment?",
        "Who is the transport supervisor to contact in case of route timing changes or emergencies?",
        "Are college buses equipped with GPS tracking for safety monitoring?",
    ],
    "FACILITIES": [
        "What are the working hours of the Central Library, and how many books can a student borrow?",
        "Is there a dedicated gymnasium, indoor sports complex, and cricket ground on campus?",
        "What medical and first-aid facilities are available on campus during college working hours?",
        "Are high-speed Wi-Fi and digital library access available to students throughout campus?",
        "What hygienic food items and meals are served in the college cafeteria and canteen?",
        "Does Sreyas offer separate incubation and maker spaces for student startups and innovation projects?",
        "Are air-conditioned auditorium and seminar halls available for technical symposia?",
        "What ATM or banking facilities are available inside or near the college premises?",
    ],
    "COLLEGE TIMINGS": [
        "What are the official daily working hours and lecture timings for B.Tech students?",
        "Is the college open on the second Saturday and Sundays for student club activities?",
        "What is the reporting time in the morning, and is there a grace period before the main gate closes?",
        "What are the designated lunch break hours for 1st year and senior engineering students?",
        "What are the working hours of the administrative office and accounts section for fee payment?",
        "Are labs open beyond regular college hours for project work and hackathon preparation?",
    ],
    "GENERAL": [
        "What is the dress code and discipline policy for first-year engineering students?",
        "How can I contact the anti-ragging cell or women grievance redressal committee?",
        "When is the annual college cultural festival and technical fest 'Sreyas Fest' scheduled?",
        "Does Sreyas have active student chapters of IEEE, CSI, IETE, and SAE?",
        "What is the procedure for obtaining a Transfer Certificate (TC) and Migration Certificate?",
        "Can parents visit the campus to meet faculty and discuss their ward's academic progress?",
        "What extracurricular activities, clubs, and sports teams are available for freshers?",
        "How can alumni get in touch with the SIET Alumni Association network?",
    ],
    "PARENT ENQUIRY": [
        "I want to check my ward's current semester attendance percentage and internal marks.",
        "Could you please share the contact number of my daughter's CSE faculty mentor / proctor?",
        "Has the college notified the date for the upcoming Parent-Teacher Meeting (PTM)?",
        "My son has been absent due to viral fever for three days; how do I submit the medical certificate?",
        "I need an update on my ward's performance in recent mid examinations and laboratory sessions.",
        "Can a parent track daily student attendance and timetable through a mobile app or portal?",
        "I would like to discuss my child's placement preparation and backlog status with the HOD.",
        "Are campus hostels safe with 24x7 security, warden surveillance, and hygienic food for girls?",
        "We want to schedule an in-person meeting with the Principal regarding branch transfer options.",
        "Could the accounts department send the official fee receipt for our income tax rebate?",
    ],
}


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def ensure_workbook_and_headers(excel_path: str) -> Tuple[openpyxl.Workbook, openpyxl.worksheet.worksheet.Worksheet]:
    """
    Load or create the workbook, ensuring the 'Enquiries' worksheet exists
    and has all 15 header columns properly configured.
    """
    os.makedirs(os.path.dirname(excel_path), exist_ok=True)

    if os.path.isfile(excel_path):
        wb = openpyxl.load_workbook(excel_path)
        if SHEET_NAME in wb.sheetnames:
            ws = wb[SHEET_NAME]
        else:
            ws = wb.create_sheet(SHEET_NAME)
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = SHEET_NAME

    # Ensure header row has all 15 columns
    header_font = Font(bold=True)
    for col_idx, col_name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        if cell.value != col_name:
            cell.value = col_name
        cell.font = header_font

    return wb, ws


def count_demo_records(ws: openpyxl.worksheet.worksheet.Worksheet) -> int:
    """
    Check existing rows (row >= 2) for demo records with domain @demo.sreyas.ac.in.
    """
    demo_count = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        # Column 6 (index 5) is Email
        email = str(row[5] or "").strip().lower()
        if DEMO_DOMAIN in email:
            demo_count += 1
    return demo_count


def get_last_enquiry_id(ws: openpyxl.worksheet.worksheet.Worksheet) -> int:
    """
    Find the highest integer sequence number among existing Enquiry IDs (e.g., ENQ0015 -> 15).
    """
    highest_id = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        val = str(row[0]).strip()
        match = _ENQUIRY_ID_PATTERN.match(val)
        if match:
            highest_id = max(highest_id, int(match.group(1)))
    return highest_id


def generate_dates_distribution(total_count: int, today: datetime) -> List[Tuple[str, str]]:
    """
    Generate 150 timestamps:
    - ~7 records with today's date
    - ~13 records from the last 1 to 7 days
    - ~130 records spread across 8 to 90 days ago
    Returns a list of (date_str, time_str) pairs.
    """
    timestamps: List[Tuple[str, str]] = []

    # 7 records today
    for _ in range(7):
        hour = random.randint(9, 17)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        timestamps.append((
            today.strftime("%Y-%m-%d"),
            f"{hour:02d}:{minute:02d}:{second:02d}"
        ))

    # 13 records from this week (1 to 7 days ago)
    for _ in range(13):
        days_ago = random.randint(1, 7)
        dt = today - timedelta(days=days_ago)
        hour = random.randint(9, 18)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        timestamps.append((
            dt.strftime("%Y-%m-%d"),
            f"{hour:02d}:{minute:02d}:{second:02d}"
        ))

    # Remaining records (130) spread across 8 to 90 days ago
    remaining = total_count - len(timestamps)
    for _ in range(remaining):
        days_ago = random.randint(8, 90)
        dt = today - timedelta(days=days_ago)
        hour = random.randint(8, 19)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        timestamps.append((
            dt.strftime("%Y-%m-%d"),
            f"{hour:02d}:{minute:02d}:{second:02d}"
        ))

    # Sort timestamps chronologically so records flow naturally over time
    timestamps.sort(key=lambda x: f"{x[0]} {x[1]}")
    return timestamps


def build_demo_enquiries(start_id: int, total_count: int = 150) -> List[List]:
    """
    Build 150 structured demo enquiry rows respecting all status, category,
    priority, and parent email requirements.
    """
    today = datetime.now()
    timestamps = generate_dates_distribution(total_count, today)

    # 1. Create list of categories matching target distribution
    categories: List[str] = []
    for cat, count in CATEGORY_DISTRIBUTION.items():
        categories.extend([cat] * count)

    # 2. Create list of statuses matching target distribution
    statuses: List[str] = []
    for status, count in STATUS_DISTRIBUTION.items():
        statuses.extend([status] * count)

    # To keep data realistic:
    # Most recent entries (today and this week) are more likely to be Pending or In Progress.
    # Older entries are more likely to be Reviewed or Closed.
    # Let's organize statuses in a realistic chronological sequence:
    # 40 Closed (oldest) + 35 Reviewed + 20 In Progress + 55 Pending (newest)
    # Then add small random perturbation so not 100% rigidly segmented.
    structured_statuses = (
        ["Closed"] * 40 +
        ["Reviewed"] * 35 +
        ["In Progress"] * 20 +
        ["Pending"] * 55
    )

    # Swap a few adjacent elements to create realistic variance without violating exact counts
    for _ in range(25):
        i = random.randint(0, total_count - 2)
        # Only swap within a small window
        j = min(total_count - 1, i + random.randint(1, 10))
        structured_statuses[i], structured_statuses[j] = structured_statuses[j], structured_statuses[i]

    # Shuffle categories while preserving the exact count
    # Keep PARENT ENQUIRY distributed across records
    random.shuffle(categories)

    rows: List[List] = []
    used_names: set = set()

    for idx in range(total_count):
        enquiry_seq = start_id + idx + 1
        enquiry_id = f"ENQ{enquiry_seq:04d}"
        date_str, time_str = timestamps[idx]
        category = categories[idx]
        status = structured_statuses[idx]

        # Generate unique Indian name
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        name = f"{first} {last}"
        email_prefix = f"{first.lower()}.{last.lower()}{enquiry_seq}"
        email = f"{email_prefix}@{DEMO_DOMAIN}"

        # Synthetic 10-digit phone starting with 9000
        phone = f"9000{random.randint(100000, 999999)}"

        # Question for the category
        q_pool = CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS["GENERAL"])
        question = random.choice(q_pool)

        # Parent mode vs Student mode
        if category == "PARENT ENQUIRY":
            mode = "Parent Mode"
            parent_email = f"parent{enquiry_seq:03d}@{DEMO_DOMAIN}"
            priority = "HIGH"
        else:
            mode = "Student Mode"
            parent_email = ""
            priority = "NORMAL"

        # Staff assignment and remarks based on status
        assigned_to = random.choice(ASSIGNED_STAFF[status])
        remarks = random.choice(STATUS_REMARKS[status])

        # Confidence score between 0.15 and 0.85
        confidence = round(random.uniform(0.15, 0.85), 4)

        row = [
            enquiry_id,
            date_str,
            time_str,
            name,
            phone,
            email,
            mode,
            category,
            confidence,
            question,
            status,
            assigned_to,
            remarks,
            parent_email,
            priority,
        ]
        rows.append(row)

    return rows


# ==============================================================================
# MAIN SEEDING ROUTINE
# ==============================================================================

def seed_demo_enquiries() -> None:
    """
    Main entry point for seeding demo enquiries.
    """
    print("=" * 70)
    print("SIET Chatbot - Demo Enquiry Seeder")
    print("=" * 70)
    print(f"Target Excel Path: {EXCEL_PATH}")

    # 1. Load workbook and fix headers if missing
    wb, ws = ensure_workbook_and_headers(EXCEL_PATH)

    # Count existing rows
    existing_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r and r[0] is not None]
    initial_count = len(existing_rows)
    print(f"Initial record count in workbook: {initial_count}")

    # 2. Idempotent check: check for demo records
    demo_count = count_demo_records(ws)
    if demo_count > 0:
        print(f"\n[IDEMPOTENT SKIP] Demo records already exist in the workbook!")
        print(f"Found {demo_count} demo records with domain '@{DEMO_DOMAIN}'.")
        print(f"Total existing records: {initial_count}")
        print("No new records were added.")
        print("=" * 70)
        wb.close()
        return

    # 3. Determine last enquiry ID
    last_id_num = get_last_enquiry_id(ws)
    print(f"Highest existing Enquiry ID sequence: ENQ{last_id_num:04d}")

    # 4. Generate ~150 demo enquiries
    target_demo_count = 150
    demo_rows = build_demo_enquiries(start_id=last_id_num, total_count=target_demo_count)

    # 5. Append rows directly to the worksheet
    for row in demo_rows:
        ws.append(row)

    # Save workbook safely
    wb.save(EXCEL_PATH)
    wb.close()
    print(f"\nSuccessfully inserted {len(demo_rows)} demo records.")

    # 6. Calculate and print summary
    total_records = initial_count + len(demo_rows)
    status_counts = Counter(row[10] for row in demo_rows)
    category_counts = Counter(row[7] for row in demo_rows)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for row in demo_rows if row[1] == today_str)

    print("\n" + "=" * 70)
    print("DEMO ENQUIRY SEED SUMMARY")
    print("=" * 70)
    print(f"Original record count : {initial_count}")
    print(f"Demo records inserted : {len(demo_rows)}")
    print(f"Total records now     : {total_records}")
    print(f"Today's records count : {today_count} (Date: {today_str})")

    print("\nStatus Distribution:")
    for status, count in status_counts.most_common():
        print(f"  - {status:<15}: {count}")

    print("\nCategory Distribution:")
    for category, count in category_counts.most_common():
        print(f"  - {category:<22}: {count}")

    print("\nSample Inserted Record (First):")
    first = demo_rows[0]
    print(f"  ID: {first[0]} | Date: {first[1]} | Name: {first[3]} | Cat: {first[7]} | Status: {first[10]}")

    print("Sample Inserted Record (Last):")
    last = demo_rows[-1]
    print(f"  ID: {last[0]} | Date: {last[1]} | Name: {last[3]} | Cat: {last[7]} | Status: {last[10]}")

    print("=" * 70)
    print("Seeding completed successfully.")


if __name__ == "__main__":
    seed_demo_enquiries()
