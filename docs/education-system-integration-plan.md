# Education System Integration Plan

## Audit

### Backend modules currently present
- `EducationInstitution`
- `EducationInstitutionMembership`
- `EducationInstitutionProgram`
- `EducationInstitutionCourse`
- `EducationInstitutionLesson`
- `EducationInstitutionClassSession`
- `EducationInstitutionMaterial`
- `EducationInstitutionAssessment` and question/submission tables
- `EducationInstitutionEvent`
- `EducationInstitutionBroadcast`
- `EducationInstitutionEnrollment`
- `EducationInstitutionBooking`

### Legacy or duplicated implementations still present
- `EducationProfile`
- `EducationProfileCourse`
- `EducationProfileModule`
- `EducationProfileRole`
- `EducationProfileRoleAssignment`
- app hooks/components built around `educationProfiles` rather than `educationInstitutions`

### Current architecture strengths
- institution-rooted operational model already exists
- course, lesson, class, material, assessment, event, broadcast, enrollment, and booking tables exist
- institution dashboard and module APIs already exist
- current mobile institution flow is already moving away from the old profile-only model

### Current architecture gaps
- some entities are still too weakly connected:
  - materials were only linked to course/lesson
  - events were not explicitly linked to program/course/class
  - broadcasts could not target programs or institution-wide notices
  - enrollments/bookings did not carry enough academic context for analytics
- staff had memberships, but no explicit assignment layer to connect them to programs/courses/classes/events/exams
- frontend still contains old education-profile codepaths and discovery models that are not aligned to the institution-rooted domain

## Target Relationship Model

### Institution layer
- `EducationInstitution`
  - operational root
  - owns academic, engagement, and analytics data
- `EducationInstitutionMembership`
  - user-to-institution association
  - access, role, and lifecycle gate

### Academic structure
- `Program`
  - belongs to institution
  - contains courses
  - can have materials, events, broadcasts, enrollments, and bookings
- `Course`
  - belongs to institution
  - optionally belongs to program
  - contains lessons, classes, materials, exams, broadcasts, enrollments, and bookings
- `Lesson`
  - belongs to institution and course
  - can have materials, assessments, class sessions, broadcasts, and enrollments
- `ClassSession`
  - belongs to institution
  - optionally belongs to course and lesson
  - can have materials, assessments, events, broadcasts, enrollments, bookings, and staff assignments
- `Material`
  - belongs to institution
  - must attach to at least one academic parent:
    - program
    - course
    - lesson
    - class session
    - assessment
- `Assessment`
  - belongs to institution
  - can attach to course, lesson, or class session
  - can have materials and staff assignments
- `Event`
  - belongs to institution
  - can attach to program, course, or class session
  - can be standard event or training session

### People and operations
- `StaffAssignment`
  - belongs to institution
  - attaches a non-student membership to one or more academic targets
  - role examples:
    - instructor
    - coordinator
    - examiner
    - advisor
    - moderator
    - event host
- `Enrollment`
  - belongs to institution
  - currently operationally enters through broadcasts
  - carries academic target context:
    - program
    - course
    - lesson
    - class session
    - event
- `Booking`
  - belongs to institution
  - currently operationally enters through broadcasts
  - carries academic target context:
    - program
    - course
    - class session
    - event

### Communication and discovery
- `Broadcast`
  - belongs to institution
  - can target:
    - institution notice
    - program
    - course
    - lesson
    - class session
    - training session
    - event

## Real-world workflow decisions

### Materials
- safest model is explicit nullable foreign keys instead of polymorphic content types
- reason:
  - easier validation
  - simpler analytics joins
  - easier frontend selectors
  - clearer ownership rules

### Broadcasts
- broadcasts remain first-class records, not just metadata blobs
- program and institution-notice broadcasts are now explicit
- institution notices intentionally do not target a narrower academic entity

### Staff
- membership answers “who belongs here”
- staff assignment answers “what are they responsible for”
- these are separate concerns and should remain separate tables

### Enrollments and bookings
- near-term compatibility is preserved by keeping broadcast-driven creation flows intact
- academic target context is still persisted on the row for reporting, detail pages, and future direct-enrollment flows

## Phase Order

### Phase 1
- audit + foundational relationship alignment
- additive schema work only
- preserve current mobile workflows

### Phase 2
- service/API alignment and detail payloads
- institution/program/course/lesson/class/student/staff relationship summaries

### Phase 3
- frontend detail pages and relationship-aware forms
- move more app flows away from legacy education-profile code

### Phase 4
- booking/appointment and broadcast targeting expansion
- direct academic bookings and appointment workflows

### Phase 5
- analytics and dashboards
- completion, attendance, exam performance, engagement, broadcast performance, booking usage, institution growth

### Phase 6
- cleanup, validation hardening, legacy retirement, and compatibility review

## Phase 1 Decisions
- do not break current education hub or institution dashboard
- keep old `EducationProfile` flows alive for now, but treat them as legacy
- make new relationships additive and institution-safe
- push file/image/video selection to device-pick flows in the mobile app

## Next recommended step
- Phase 2 should add relationship-aware detail payload endpoints for:
  - program
  - course
  - lesson
  - class session
  - student/member
  - staff/member
- then the mobile dashboard modules should switch from flat CRUD pages to real interconnected detail workspaces

## Public Readiness Reset

### Problem statement
- the current system is strong on institution administration and broadcast-backed operations
- the current system is weak on learner consumption and navigation clarity
- the current mobile experience still mixes three different concerns:
  - institution administration
  - public discovery and broadcast entry
  - learner course consumption

### Non-negotiable foundations to keep
- multiple institutions per user
- institution-rooted data ownership
- broadcast as a discovery and public distribution layer
- booking and wallet settlement in `KISC`
- role-aware membership and staff assignment

### Public-ready product architecture

#### Surface 1: Institution Admin Workspace
- audience:
  - owners
  - managers
  - administrators
  - lecturers
  - coordinators
- purpose:
  - create and manage institutions
  - manage programs, courses, lessons, classes, materials, assessments, events, broadcasts, bookings, enrollments, people, and settings
- this is the current strongest part of the system and should remain separate

#### Surface 2: Public Discovery and Broadcast
- audience:
  - all users
  - prospective students
  - members
- purpose:
  - discover institutions, programs, courses, classes, lessons, events, and training sessions
  - decide whether to preview, enroll, book, or pay
- broadcast cards should remain the public entry point, not the learning workspace itself

#### Surface 3: Learner Consumption Workspace
- audience:
  - enrolled students
  - booked learners
  - approved members with access
- purpose:
  - take a program or course
  - move through modules
  - open lessons
  - watch videos
  - read materials
  - attend classes
  - complete assessments
  - track grades, progress, certificates, and resume state
- this is the missing core LMS layer

### Coursera-aligned learner model
- `Program`
  - groups courses
  - may expose program-wide milestones, events, and announcements
- `Course`
  - the main learner container
  - should expose:
    - what you will learn
    - instructors
    - module outline
    - materials
    - classes
    - assessments
    - progress
    - certificate path
- `CourseModule`
  - learner-facing grouping layer similar to week/module blocks
  - each module contains ordered learning items
- `LearningItem`
  - typed item displayed in the learner flow:
    - lesson
    - material/reading
    - video
    - class session
    - assessment
    - event
    - announcement/broadcast reference

### Why the current system feels difficult
- too many entities are shown as peer-level navigation targets
- learner flow is not separated from admin flow
- there is no dominant course-taking page
- materials, lessons, classes, assessments, events, and broadcasts do not yet resolve into one ordered learner sequence
- progress, resume, completion, and certificate UX are not first-class

### Public readiness phase order

#### Public Phase 1: Learner information architecture
- define the three-surface split formally:
  - admin
  - public discovery
  - learner consumption
- document the learner-first hierarchy:
  - institution
  - program
  - course
  - module
  - item
- stop adding UX that mixes admin and learner tasks on the same screens

#### Public Phase 2: Course module and learning sequence foundation
- add a learner-facing module layer under courses
- support ordered learning items per module
- map existing lessons, materials, class sessions, assessments, and events into that sequence
- keep existing admin records intact; do not replace them with a second shadow system

#### Public Phase 3: Public landing pages and broadcast entry
- module-aware public landing pages for:
  - institution
  - program
  - course
  - event
  - class session
- broadcast cards should open these landing pages
- support preview, enroll, and book entry paths cleanly

#### Public Phase 4: Learner course workspace
- create the real student-facing course experience:
  - continue learning
  - module outline
  - current item
  - next item
  - grades
  - deadlines or schedule
  - resume state

#### Public Phase 5: Item consumption
- lesson reader/player
- material viewer and download handling
- class attendance and session joining
- exam-taking flow
- event participation flow

#### Public Phase 6: Progress, completion, and certificate
- per-item completion
- per-module completion
- per-course completion
- grade aggregation
- certificate eligibility and issuance

#### Public Phase 7: Navigation, polish, and accessibility
- simplify navigation
- add stronger breadcrumbs, tabs, and progress indicators
- make learner surfaces visually coherent and faster to understand
- remove or hide legacy dead-end education paths

#### Public Phase 8: Cleanup and retirement
- retire or reduce old `EducationProfile` and other legacy entry points
- remove duplicated learner/discovery codepaths
- keep compatibility only where necessary for existing data or links

### Current execution priority
- the next implementation should prioritize learner consumption, not more admin-only CRUD
- the first concrete build target is:
  - course landing page
  - module outline
  - learner course workspace
  - item progression
- current completed public-readiness state:
  - Phase 1: learner information architecture
  - Phase 2: module sequence foundation
  - Phase 3: public detail + creator module authoring
  - Phase 4: learner workspace + enrollment-backed progress
  - Phase 5: item consumption payloads + learner consumption panels
  - Phase 6: learner actions for assessments and attendance + learner insights
  - Phase 7: embedded media/document consumption + certificate preview/issuance flow
  - Phase 8: learner navigation polish + certificate share metadata flow
  - Phase 9: public certificate verification/share + final discovery/enrollment public UX cleanup
- public-readiness status:
  - the foundational public architecture, learner flow, payments, certificates, and discovery surfaces are now implemented
  - discovery now includes institution spotlights and learner-home summary cards
  - public course detail now includes institution summary, trust signals, and teaching context
  - remaining work should be treated as targeted QA and visual refinement, not new foundational phase work
