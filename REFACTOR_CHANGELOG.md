# eduPro ERP — Authentication & Academic Approval Refactor

## Overview

This document describes every change made across the `accounts`, `academics`,
and `portal` apps in the authentication and academic-approval refactor.

---

## 1. `accounts` App

### `models.py`

| Change | Detail |
|---|---|
| `EduProUser.is_active` default | Changed from `True` → **`False`**. New accounts are inactive until approved. |
| `EduProUser.approved_by` | New FK to self — records who activated the account. |
| `EduProUser.approved_at` | New `DateTimeField` — records when the account was activated. |
| `EduProUserManager.create_user()` | Now forces `is_active=False` by default. |
| `EduProUserManager.create_approved_user()` | New method — called by portal approval flow; sets `is_active=True`. |
| `EduProUser.has_responsibility()` | New method — checks `UserStaffRole` for a given responsibility. |
| `EduProUser.is_hod_of(dept)` | New method — checks HOD responsibility scoped to a specific department. |
| `EduProUser.get_hod_departments()` | New method — returns QuerySet of departments where user is HOD. |
| `EduProUser.get_active_responsibilities()` | New method — returns list of active responsibility strings. |
| `EduProUser.get_dashboard_url()` | New method — returns role-appropriate dashboard URL. |
| **`UserStaffRole`** | **Entirely new model.** Grants a `StaffResponsibility` to a user, optionally scoped to a `Department`. One row per responsibility. `unique_together = (user, responsibility, department)`. |
| **`StaffResponsibility`** | New `TextChoices` enum: TEACHER, HOD, DEAN, PROGRAM_COORDINATOR, ADMISSIONS_OFFICER, EXAMINATIONS_OFFICER, COUNSELOR. |

### `decorators.py` (full rewrite)

| Decorator | Purpose |
|---|---|
| `anonymous_required()` | Redirects already-authenticated users away from login/register. |
| `active_required` | Logs out and redirects inactive users with a clear message. |
| `role_required(*roles)` | Generic role gate; superuser always passes. |
| `admin_required` | Requires `role=ADMIN` or superuser. |
| `teacher_required` | Requires `role=TEACHER` or ADMIN or superuser. |
| `student_required` | Requires `role=STUDENT` or superuser. |
| `responsibility_required(*responsibilities)` | Checks `UserStaffRole`; user must hold ≥1 listed responsibility. |
| `hod_required` | User must be HOD of at least one department. |
| `hod_of_dept_required(dept_kwarg)` | User must be HOD of the department identified by a URL kwarg. |

### `forms.py`

| Change | Detail |
|---|---|
| `RegisterForm` removed | Replaced by `PendingRegistrationForm`. |
| **`PendingRegistrationForm`** | New form. Saves user with `is_active=False`. No `login()` call. Restricted to staff (role=TEACHER default). Admission applicants directed to portal. |
| **`UserStaffRoleForm`** | New form. Admin grants responsibilities. Validates department is set for HOD. |
| `LoginForm.clean()` | Now explicitly raises `ValidationError` with a message when `is_active=False`. |

### `views.py`

| View | Change |
|---|---|
| `register_view` | No `login()` after save. Returns `registration_pending.html` instead of redirecting to dashboard. |
| `login_view` | Unchanged logic; form itself rejects inactive users. |
| `teacher_dashboard` | **New combined dashboard.** Fetches `responsibilities`, `is_hod`, `hod_departments`, `pending_sheets`. Renders unified context for multi-role users. |
| `admin_dashboard` | Shows `pending_users` count and `pending_applications` from portal. |
| `dashboard_redirect` | New single-entry-point view; routes to role-appropriate dashboard. |
| **`pending_users_view`** | New — lists all `is_active=False` users for admin review. |
| **`approve_user`** | New — sets `is_active=True`, records `approved_by`, `approved_at`. |
| **`reject_user`** | New — permanently deletes the pending account. |
| **`staff_roles_view`** | New — admin views and assigns `UserStaffRole` records for a user. |
| **`revoke_staff_role`** | New — sets `UserStaffRole.is_active=False`. |

### `urls.py`

New endpoints added:

```
accounts/pending/                          pending_users
accounts/pending/<pk>/approve/             approve_user
accounts/pending/<pk>/reject/              reject_user
accounts/users/<pk>/roles/                 staff_roles
accounts/users/roles/<role_pk>/revoke/     revoke_staff_role
accounts/dashboard/                        dashboard_redirect  (new entry point)
accounts/dashboard/student/                student_dashboard
accounts/dashboard/teacher/                teacher_dashboard
accounts/dashboard/admin/                  dashboard (admin)
```

### `admin.py`

- `UserStaffRoleInline` added to `EduProUserAdmin`.
- `approve_users` bulk action — sets `is_active=True` on selected users.
- `deactivate_users` bulk action.
- `is_active` shown prominently in list display.

### Migration: `0003_userstaffrole_user_approval_fields.py`

- Adds `EduProUser.approved_by`, `approved_at`.
- Alters `EduProUser.is_active` default to `False`.
- Creates `UserStaffRole` table with unique constraint.

---

## 2. `academics` App

### `models.py`

| Change | Detail |
|---|---|
| All existing models | Unchanged. |
| **`ResultSheet`** | **Entirely new model.** |

#### `ResultSheet` fields

| Field | Purpose |
|---|---|
| `offering` | FK to `CourseOffering`. |
| `department` | FK to `Department` — copied from course's department on creation. Used to scope HOD authority. |
| `submitted_by` | FK to teacher who owns this sheet. |
| `status` | `DRAFT → SUBMITTED → HOD_APPROVED → FINALIZED` (TextChoices). |
| `submitted_at` | Timestamp of submission. |
| `hod_approved_by` | FK to HOD who approved. |
| `hod_approved_at` | Timestamp of HOD approval. |
| `finalized_by` | FK to admin who finalized. |
| `finalized_at` | Timestamp of finalization. |
| `notes` | Free-text notes. |

#### `ResultSheet` methods

| Method | What it enforces |
|---|---|
| `submit(actor)` | Must be DRAFT; actor must have allocation for offering. |
| `hod_approve(actor)` | Must be SUBMITTED; actor must be HOD of `sheet.department`. |
| `finalize(actor)` | Must be HOD_APPROVED; actor must be admin/superuser. |
| `revert_to_draft(actor)` | Must be SUBMITTED; actor must be HOD of dept or admin. |
| `is_locked` property | True when status ≥ HOD_APPROVED. Prevents edits. |
| `create_for_offering()` | Class factory; auto-sets department; prevents duplicates. |

### `forms.py`

| Change | Detail |
|---|---|
| **`ResultSheetForm`** | New form. `offering` queryset scoped to teacher's own active `CourseAllocation` — teachers cannot submit results for courses they don't teach. |

### `views.py`

| View | Access | Enforcement |
|---|---|---|
| `result_sheet_list` | teacher/HOD/admin | HOD sees only dept sheets; teacher sees own only. |
| `result_sheet_create` | `@teacher_required` | Form restricts offering choices to own allocations. |
| `result_sheet_detail` | teacher/HOD/admin | `PermissionDenied` if not owner, HOD of dept, or admin. |
| `result_sheet_submit` | `@teacher_required` | `submitted_by=request.user` guard in queryset. |
| `result_sheet_hod_approve` | `@hod_required` | `sheet.hod_approve(request.user)` checks department match. |
| `result_sheet_finalize` | `@admin_required` | `sheet.finalize(request.user)` checks admin role. |
| `result_sheet_revert` | HOD or admin | `sheet.revert_to_draft(request.user)` validates authority. |
| `result_sheet_admin_list` | `@admin_required` | All sheets, status-filterable. |
| `my_courses` | `@teacher_required` | Strictly `filter(teacher=request.user)`. |
| `academics_dashboard` | all roles | Dynamic context based on role + responsibilities. |

### `urls.py`

New endpoints:

```
academics/result-sheets/                        result_sheet_list
academics/result-sheets/admin/                  result_sheet_admin_list
academics/result-sheets/add/                    result_sheet_create
academics/result-sheets/<pk>/                   result_sheet_detail
academics/result-sheets/<pk>/submit/            result_sheet_submit
academics/result-sheets/<pk>/hod-approve/       result_sheet_hod_approve
academics/result-sheets/<pk>/finalize/          result_sheet_finalize
academics/result-sheets/<pk>/revert/            result_sheet_revert
```

### `admin.py`

- `ResultSheetAdmin` registered with `finalize_sheets` bulk action.
- All audit fields (`submitted_at`, `hod_approved_at`, etc.) read-only.

### Migration: `0002_resultsheet.py`

- Creates `ResultSheet` table with unique constraint on `(offering, submitted_by)`.

---

## 3. `portal` App

### `models.py`

| Change | Detail |
|---|---|
| **`AdmissionCycle`** | **New model.** Controls intake windows. `save()` enforces single active cycle. `is_open` property checks date range. |
| `AdmissionApplication.cycle` | New FK to `AdmissionCycle`. |
| `AdmissionApplication.status` | Extended with `WITHDRAWN` choice. |
| `AdmissionApplication.reviewed_by/at` | New audit fields for reviewing stage. |
| `AdmissionApplication.approved_by/at` | New audit fields for approval. |
| `AdmissionApplication.rejected_by/at/reason` | New audit fields for rejection. |
| `AdmissionApplication.user` | New OneToOne FK — links to provisioned `EduProUser` after approval. |
| `AdmissionApplication.approve()` | **New method.** Atomic: creates `EduProUser` (active), creates `StudentProfile`, links back. Handles existing email edge-case. |
| `AdmissionApplication.reject()` | **New method.** Records actor and reason; no user account created. |
| `AdmissionApplication.mark_reviewing()` | **New method.** PENDING → REVIEWING. |
| `AdmissionApplication.withdraw()` | **New method.** Applicant self-withdrawal. |
| **`DocumentRequest`** | **New model.** Admissions officer requests additional documents. |

### `forms.py`

| Form | Purpose |
|---|---|
| `AdmissionApplicationForm` | Public form. Scopes program choices to active programs. Guards against duplicate applications per cycle+email. |
| `ApplicationStatusCheckForm` | Reference + email lookup, no login needed. |
| `ApplicationRejectForm` | Mandatory rejection reason (min 20 chars). |
| `ApplicationReviewForm` | Internal notes. |
| `DocumentRequestForm` | Document name + instructions. |
| `AdmissionCycleForm` | Admin cycle management. |

### `views.py`

| View | Access | Purpose |
|---|---|---|
| `application_form` | Public | Checks cycle is open; guards against duplicate submissions. |
| `application_confirmed` | Public | Post-submit success. |
| `application_status` | Public | Ref + email lookup. |
| `application_withdraw` | Public | Applicant self-withdrawal. |
| `admissions_dashboard` | Admissions/admin | Status counts + recent pending. |
| `application_list` | Admissions/admin | Filterable by status, cycle, search. |
| `application_detail` | Admissions/admin | Full detail + doc requests. |
| `application_review` | Admissions/admin | PENDING → REVIEWING. |
| `application_approve` | Admissions/admin | Calls `application.approve()` → provisions `EduProUser`. |
| `application_reject` | Admissions/admin | Calls `application.reject()` with reason. |
| `document_request_create` | Admissions/admin | Requests missing docs. |
| `document_request_fulfill` | Admissions/admin | Marks doc request done. |
| `cycle_list/create/edit` | Admin only | Admission cycle management. |

Access guard: `_admissions_required` decorator — admits both `ADMIN` and `ADMISSIONS_OFFICER` responsibility.

### `admin.py`

- `AdmissionCycleAdmin` registered.
- `AdmissionApplicationAdmin` with `mark_reviewing`, `approve_applications`, `reject_applications` bulk actions.
- `DocumentRequestAdmin` registered.
- `DocumentRequestInline` added to application admin.

### Migration: `0002_admissioncycle_documentrequest_updated_application.py`

- Creates `AdmissionCycle`.
- Adds new fields to `AdmissionApplication`.
- Creates `DocumentRequest`.

---

## 4. Approval Flows Summary

### Staff Registration

```
User fills register form
  → EduProUser created (is_active=False)
  → "Registration Received" page shown (NO login)
  → Admin sees user in /accounts/pending/
  → Admin clicks Approve → is_active=True, approved_by/at recorded
  → User can now log in
```

### Student Admission

```
Applicant fills /portal/apply/
  → AdmissionApplication created (status=PENDING, no user account)
  → Admissions officer reviews → REVIEWING
  → Officer approves:
      → AdmissionApplication.approve() called (atomic)
      → EduProUser created (is_active=True, role=student)
      → StudentProfile created and linked to program
      → application.user = new_user
  → Student logs in with credentials
```

### Result Sheet

```
Teacher creates sheet → DRAFT
  (offering limited to teacher's own allocations)
Teacher submits → SUBMITTED
  (validates teacher has allocation for offering)
HOD approves → HOD_APPROVED
  (validates user is_hod_of(sheet.department))
Admin finalizes → FINALIZED
  (sheet is now locked / immutable)

HOD or Admin can revert SUBMITTED → DRAFT
  (allows teacher to fix errors)
```

---

## 5. Multi-Responsibility Dashboard

The `teacher_dashboard` view inspects `request.user.get_active_responsibilities()`
and populates the template context accordingly:

| User responsibilities | Modules shown |
|---|---|
| TEACHER only | My Courses panel |
| TEACHER + HOD | My Courses + HOD pending sheets panel |
| TEACHER + DEAN | My Courses + faculty overview (stub) |
| TEACHER + HOD + PROGRAM_COORDINATOR | All three panels |

The template uses `{% if is_hod %}` guards to conditionally render each module,
so no separate dashboard URL is needed per role combination.

---

## 6. Files Changed / Created

```
accounts/
  models.py          ← modified (UserStaffRole, approval fields, helpers)
  decorators.py      ← full rewrite (8 decorators)
  forms.py           ← PendingRegistrationForm, UserStaffRoleForm added
  views.py           ← register_view, teacher_dashboard, pending approval views
  urls.py            ← pending/approve/reject/roles endpoints added
  admin.py           ← UserStaffRoleInline, bulk approve action
  migrations/
    0003_userstaffrole_user_approval_fields.py  ← new

academics/
  models.py          ← ResultSheet added
  forms.py           ← ResultSheetForm added
  views.py           ← result sheet views, my_courses strictened
  urls.py            ← result sheet URLs added
  admin.py           ← ResultSheetAdmin added
  migrations/
    0002_resultsheet.py  ← new

portal/
  models.py          ← AdmissionCycle, DocumentRequest, approve/reject methods
  forms.py           ← all forms (new file)
  views.py           ← full rewrite
  urls.py            ← full rewrite
  admin.py           ← cycle, application, doc request admins

templates/
  accounts/registration_pending.html  ← new
  accounts/pending_users.html         ← new
  accounts/teacher_dashboard.html     ← new (combined multi-role)
  academics/result_sheet_detail.html  ← new (pipeline + action buttons)
```
