from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.broadcasts.models import (
    EducationAcademicRecordStatus,
    EducationBroadcastKind,
    EducationBroadcastStatus,
    EducationEnrollmentStatus,
    EducationInstitution,
    EducationInstitutionBroadcast,
    EducationInstitutionClass,
    EducationInstitutionCourse,
    EducationInstitutionEnrollment,
    EducationInstitutionMembership,
    EducationInstitutionMembershipRole,
    EducationInstitutionMembershipStatus,
    EducationInstitutionProgram,
    EducationInstitutionStaffAssignment,
    EducationInstitutionStaffAssignmentRole,
    EducationInstitutionStaffAssignmentStatus,
)
from apps.broadcasts.serializers import EducationInstitutionEnrollmentSerializer
from apps.broadcasts.education_communication_sync import (
    ensure_class_group,
    ensure_course_channel,
    ensure_program_community,
    sync_enrollment_communication,
    sync_staff_assignment_communication,
    revoke_staff_assignment_communication,
)
from apps.channels.models import Channel
from apps.chat.models import ConversationMember
from apps.communities.models import Community, CommunityMembership, CommunityMembershipStatus, CommunityRole
from apps.groups.models import Group, GroupMembership, GroupRole
from apps.partners.models import Partner


def _make_user(User, phone_suffix: str, username: str):
    return User.objects.create_user(
        phone=f'5558{phone_suffix}', username=username, password='secret', country='NG',
    )


class EducationProgramClassCourseHierarchyTests(APITestCase):
    """Task: 'Complete Program -> Class -> Course relationships' + Class CRUD.
    No entity in the hierarchy is required to have a parent - a Course can
    be standalone, sit directly under a Program, or sit under a Class
    (which may itself be standalone or under a Program)."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900001', 'hier_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Hierarchy Academy')

    def _classes_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/classes/'

    def _class_detail_url(self, class_id):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/classes/{class_id}/'

    def _courses_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/courses/'

    def _programs_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/programs/'

    def test_standalone_class_creation(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self._classes_url(), {'name': 'Sunday School Class'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # A read-only dotted-source field (program_id -> program.id) is
        # omitted from the payload entirely when the FK is null - DRF's
        # Field.get_attribute treats the AttributeError from traversing
        # None.id as SkipField for a non-required field, rather than
        # serializing it as null. Same convention the rest of this file's
        # program_id/course_id/channel_id/etc. fields already follow.
        self.assertIsNone(response.data['class'].get('program_id'))

    def test_class_creation_inside_program(self):
        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Diploma Program')
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._classes_url(), {'name': 'Year 1 Cohort', 'program_id': str(program.id)}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['class']['program_id'], str(program.id))

    def test_program_can_contain_multiple_classes(self):
        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Multi Program')
        EducationInstitutionClass.objects.create(institution=self.institution, program=program, name='Cohort A')
        EducationInstitutionClass.objects.create(institution=self.institution, program=program, name='Cohort B')
        self.assertEqual(program.classes.count(), 2)

    def test_standalone_course_creation(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self._courses_url(), {'title': 'Standalone Course'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['course'].get('program_id'))
        self.assertIsNone(response.data['course'].get('institution_class_id'))

    def test_course_attached_to_class_inherits_no_separate_record(self):
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Cohort C')
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._courses_url(),
            {'title': 'Class Course', 'institution_class_id': str(institution_class.id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['course']['institution_class_id'], str(institution_class.id))
        # Exactly one Course row exists for this title - attaching to a
        # Class must not create a second, duplicate Course record.
        self.assertEqual(
            EducationInstitutionCourse.objects.filter(institution=self.institution, title='Class Course').count(),
            1,
        )

    def test_nested_program_class_course_creation(self):
        self.client.force_authenticate(self.owner)
        program_resp = self.client.post(self._programs_url(), {'title': 'Nested Program'}, format='json')
        self.assertEqual(program_resp.status_code, status.HTTP_201_CREATED, program_resp.data)
        program_id = program_resp.data['program']['id']

        class_resp = self.client.post(
            self._classes_url(), {'name': 'Nested Class', 'program_id': program_id}, format='json',
        )
        self.assertEqual(class_resp.status_code, status.HTTP_201_CREATED, class_resp.data)
        class_id = class_resp.data['class']['id']

        course_resp = self.client.post(
            self._courses_url(),
            {'title': 'Nested Course', 'institution_class_id': class_id},
            format='json',
        )
        self.assertEqual(course_resp.status_code, status.HTTP_201_CREATED, course_resp.data)
        course = EducationInstitutionCourse.objects.get(id=course_resp.data['course']['id'])
        self.assertEqual(str(course.institution_class_id), class_id)
        # The course did not have to also set `program` directly - the
        # class's own program is the resolved source of truth.
        self.assertEqual(str(course.institution_class.program_id), program_id)

    def test_class_update_and_delete(self):
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Editable Class')
        self.client.force_authenticate(self.owner)
        patch_resp = self.client.patch(
            self._class_detail_url(institution_class.id), {'name': 'Renamed Class'}, format='json',
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK, patch_resp.data)
        self.assertEqual(patch_resp.data['class']['name'], 'Renamed Class')

        delete_resp = self.client.delete(self._class_detail_url(institution_class.id))
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(EducationInstitutionClass.objects.filter(id=institution_class.id).exists())

    def test_deleting_class_detaches_course_instead_of_deleting_it(self):
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Doomed Class')
        course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Surviving Course', institution_class=institution_class,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.delete(self._class_detail_url(institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        course.refresh_from_db()
        self.assertIsNone(course.institution_class_id)


class EducationClassPermissionTests(APITestCase):
    """Task: 'Make sure a user cannot access or mutate another
    institution's/program's/class's/course's data merely by changing an
    ID in a request.' Also covers write access requiring manage rights,
    not just institution membership."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900101', 'perm_owner')
        self.student = _make_user(User, '900102', 'perm_student')
        self.outsider = _make_user(User, '900103', 'perm_outsider')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Permission Academy')
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=self.student,
            role=EducationInstitutionMembershipRole.STUDENT, status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        self.other_institution = EducationInstitution.objects.create(owner=self.outsider, name='Other Academy')
        self.institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Guarded Class')

    def _class_detail_url(self, institution_id, class_id):
        return f'/api/v1/broadcasts/education/institutions/{institution_id}/classes/{class_id}/'

    def _classes_url(self, institution_id):
        return f'/api/v1/broadcasts/education/institutions/{institution_id}/classes/'

    def test_student_cannot_create_class(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(self._classes_url(self.institution.id), {'name': 'Illicit Class'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_student_cannot_delete_class(self):
        self.client.force_authenticate(self.student)
        response = self.client.delete(self._class_detail_url(self.institution.id, self.institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

    def test_outsider_cannot_even_see_the_institution(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self._classes_url(self.institution.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_cannot_reach_class_by_swapping_institution_id_in_url(self):
        # The class belongs to self.institution; requesting it through
        # self.other_institution's URL (which self.outsider legitimately
        # owns) must 404, not leak the class or 403 in a way that
        # confirms it exists.
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self._class_detail_url(self.other_institution.id, self.institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)

    def test_owner_can_full_crud_their_own_class(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._class_detail_url(self.institution.id, self.institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)


class EducationCommunicationSyncPropagationTests(APITestCase):
    """Task: 'Complete enrollment and membership propagation' + 'Partner
    Account integration' - verified through real live flows (the actual
    enrollment action view / staff assignment views), not just calling
    the sync helper functions directly, per the explicit instruction that
    helper-function-only coverage is insufficient."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900201', 'sync_owner')
        self.learner = _make_user(User, '900202', 'sync_learner')
        self.staffer = _make_user(User, '900203', 'sync_staffer')
        self.partner = Partner.objects.create(owner=self.owner, name='Sync Partner', slug='sync-partner-epc')
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Synced Academy', partner=self.partner,
        )
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=self.staffer,
            role=EducationInstitutionMembershipRole.LECTURER, status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        self.program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Synced Program')
        self.institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, program=self.program, name='Synced Class',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, institution_class=self.institution_class, title='Synced Course',
        )
        # EducationInstitutionEnrollment.broadcast is a required FK (every
        # real enrollment-creating view sets it) - discoverability/
        # enrollment is mediated through a Broadcast record, not the
        # course/program/class directly.
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=self.course, program=self.program,
        )

    def _enroll_via_admin_action(self, enrollment, action):
        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/enrollments/{enrollment.id}/action/'
        self.client.force_authenticate(self.owner)
        return self.client.post(url, {'action': action}, format='json')

    def test_no_communication_created_without_a_partner(self):
        unpartnered = EducationInstitution.objects.create(owner=self.owner, name='Unpartnered Academy')
        program = EducationInstitutionProgram.objects.create(institution=unpartnered, title='No Partner Program')
        course = EducationInstitutionCourse.objects.create(institution=unpartnered, program=program, title='No Partner Course')
        broadcast = EducationInstitutionBroadcast.objects.create(
            institution=unpartnered, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=course, program=program,
        )
        enrollment = EducationInstitutionEnrollment.objects.create(
            institution=unpartnered, broadcast=broadcast, program=program, course=course, user=self.learner,
            status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(enrollment)
        program.refresh_from_db()
        course.refresh_from_db()
        self.assertIsNone(program.community_id)
        self.assertIsNone(course.channel_id)

    def test_course_enrollment_propagates_to_channel_membership(self):
        enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=self.broadcast, program=self.program, course=self.course,
            institution_class=self.institution_class, user=self.learner,
            status=EducationEnrollmentStatus.PENDING,
        )
        response = self._enroll_via_admin_action(enrollment, 'enroll')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.course.refresh_from_db()
        self.assertIsNotNone(self.course.channel_id)
        channel = Channel.objects.get(id=self.course.channel_id)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=channel.conversation, user=self.learner, left_at__isnull=True,
            ).exists()
        )
        # Class + Program get the same live enrollment (Course enrollment
        # sets institution_class/program too) - membership propagates to
        # every ancestor's communication space, not only the most
        # specific one.
        self.institution_class.refresh_from_db()
        self.program.refresh_from_db()
        group = Group.objects.get(id=self.institution_class.group_id)
        self.assertTrue(GroupMembership.objects.filter(group=group, user=self.learner, left_at__isnull=True).exists())
        community = Community.objects.get(id=self.program.community_id)
        self.assertTrue(
            CommunityMembership.objects.filter(
                community=community, user=self.learner, status=CommunityMembershipStatus.ACTIVE,
            ).exists()
        )

    def test_cancelling_enrollment_removes_membership(self):
        enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=self.broadcast, program=self.program, course=self.course,
            institution_class=self.institution_class, user=self.learner,
            status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(enrollment)
        channel = Channel.objects.get(id=self.course.channel_id)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=channel.conversation, user=self.learner, left_at__isnull=True,
            ).exists()
        )

        response = self._enroll_via_admin_action(enrollment, 'cancel')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(
            ConversationMember.objects.filter(
                conversation=channel.conversation, user=self.learner, left_at__isnull=True,
            ).exists()
        )

    def test_sync_is_idempotent_no_duplicate_rooms_or_memberships(self):
        enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=self.broadcast, program=self.program, course=self.course,
            institution_class=self.institution_class, user=self.learner,
            status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(enrollment)
        sync_enrollment_communication(enrollment)
        sync_enrollment_communication(enrollment)

        self.course.refresh_from_db()
        self.institution_class.refresh_from_db()
        self.program.refresh_from_db()
        self.assertEqual(Channel.objects.filter(id=self.course.channel_id).count(), 1)
        self.assertEqual(Group.objects.filter(id=self.institution_class.group_id).count(), 1)
        self.assertEqual(Community.objects.filter(id=self.program.community_id).count(), 1)

        channel = Channel.objects.get(id=self.course.channel_id)
        self.assertEqual(
            ConversationMember.objects.filter(conversation=channel.conversation, user=self.learner).count(), 1,
        )

    def test_ensure_functions_are_idempotent_and_reuse_existing_rooms(self):
        community_first = ensure_program_community(self.program)
        community_second = ensure_program_community(self.program)
        self.assertEqual(community_first.id, community_second.id)

        group_first = ensure_class_group(self.institution_class)
        group_second = ensure_class_group(self.institution_class)
        self.assertEqual(group_first.id, group_second.id)

        channel_first = ensure_course_channel(self.course)
        channel_second = ensure_course_channel(self.course)
        self.assertEqual(channel_first.id, channel_second.id)

    def test_staff_assignment_propagates_admin_role(self):
        membership = self.institution.memberships.get(user=self.staffer)
        assignment = EducationInstitutionStaffAssignment.objects.create(
            institution=self.institution,
            membership=membership,
            institution_class=self.institution_class,
            role=EducationInstitutionStaffAssignmentRole.INSTRUCTOR,
            status=EducationInstitutionStaffAssignmentStatus.ACTIVE,
        )
        sync_staff_assignment_communication(assignment)
        group = Group.objects.get(id=self.institution_class.group_id)
        self.assertTrue(
            GroupMembership.objects.filter(group=group, user=self.staffer, role=GroupRole.ADMIN).exists()
        )

    def test_revoking_staff_assignment_demotes_but_does_not_remove_membership(self):
        membership = self.institution.memberships.get(user=self.staffer)
        assignment = EducationInstitutionStaffAssignment.objects.create(
            institution=self.institution,
            membership=membership,
            program=self.program,
            role=EducationInstitutionStaffAssignmentRole.COORDINATOR,
            status=EducationInstitutionStaffAssignmentStatus.ACTIVE,
        )
        sync_staff_assignment_communication(assignment)
        community = Community.objects.get(id=self.program.community_id)
        self.assertEqual(
            CommunityMembership.objects.get(community=community, user=self.staffer).role, CommunityRole.ADMIN,
        )

        revoke_staff_assignment_communication(assignment)
        membership_row = CommunityMembership.objects.get(community=community, user=self.staffer)
        self.assertEqual(membership_row.role, CommunityRole.MEMBER)
        self.assertEqual(membership_row.status, CommunityMembershipStatus.ACTIVE)

    def test_staff_assignment_detail_view_wires_sync_on_create_and_delete(self):
        membership = self.institution.memberships.get(user=self.staffer)
        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/staff-assignments/'
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            url,
            {
                'membership_id': str(membership.id),
                'institution_class_id': str(self.institution_class.id),
                'role': 'instructor',
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # The view operated on its own freshly-queried EducationInstitutionClass
        # instance, not this in-memory self.institution_class - must
        # refresh before reading the group_id it just populated.
        self.institution_class.refresh_from_db()
        group = Group.objects.get(id=self.institution_class.group_id)
        self.assertTrue(
            GroupMembership.objects.filter(group=group, user=self.staffer, role=GroupRole.ADMIN).exists()
        )

        assignment_id = response.data['staff_assignment']['id']
        delete_url = f'{url}{assignment_id}/'
        delete_response = self.client.delete(delete_url)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            GroupMembership.objects.get(group=group, user=self.staffer).role, GroupRole.MEMBER,
        )


class EducationPartnerDisconnectTests(APITestCase):
    """Task: 'Disconnecting a Partner Account must NOT delete Education
    data, students, enrollments, Courses, Classes, or Programs.'"""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900301', 'disc_owner')
        self.learner = _make_user(User, '900302', 'disc_learner')
        self.partner = Partner.objects.create(owner=self.owner, name='Disconnect Partner', slug='disc-partner-epc')
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Disconnect Academy', partner=self.partner,
        )
        self.program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Disc Program')
        self.institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, program=self.program, name='Disc Class',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, institution_class=self.institution_class, title='Disc Course',
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=self.course, program=self.program,
        )
        self.enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=self.broadcast, program=self.program,
            institution_class=self.institution_class,
            course=self.course, user=self.learner, status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(self.enrollment)

    def test_disconnect_preserves_all_education_and_communication_data(self):
        community_id = self.program.community_id
        group_id = None
        self.institution_class.refresh_from_db()
        group_id = self.institution_class.group_id
        self.program.refresh_from_db()
        community_id = self.program.community_id
        self.course.refresh_from_db()
        channel_id = self.course.channel_id
        self.assertIsNotNone(community_id)
        self.assertIsNotNone(group_id)
        self.assertIsNotNone(channel_id)

        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/partner/'
        self.client.force_authenticate(self.owner)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.institution.refresh_from_db()
        self.assertIsNone(self.institution.partner_id)

        # Nothing on the education side was deleted.
        self.assertTrue(EducationInstitutionProgram.objects.filter(id=self.program.id).exists())
        self.assertTrue(EducationInstitutionClass.objects.filter(id=self.institution_class.id).exists())
        self.assertTrue(EducationInstitutionCourse.objects.filter(id=self.course.id).exists())
        self.assertTrue(EducationInstitutionEnrollment.objects.filter(id=self.enrollment.id).exists())

        # Nothing on the communication side was deleted either - existing
        # rooms/membership are left exactly as they were.
        self.assertTrue(Community.objects.filter(id=community_id).exists())
        self.assertTrue(Group.objects.filter(id=group_id).exists())
        self.assertTrue(Channel.objects.filter(id=channel_id).exists())
        channel = Channel.objects.get(id=channel_id)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=channel.conversation, user=self.learner, left_at__isnull=True,
            ).exists()
        )

    def test_no_new_communication_created_after_disconnect(self):
        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/partner/'
        self.client.force_authenticate(self.owner)
        self.client.delete(url)
        # The view cleared partner on its own freshly-queried institution
        # row - self.institution in memory still has the old partner_id
        # (and EducationInstitutionCourse.create below would inherit that
        # stale cached relation if passed the object directly) unless
        # refreshed.
        self.institution.refresh_from_db()

        new_course = EducationInstitutionCourse.objects.create(
            institution=self.institution, title='Post Disconnect Course',
        )
        new_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=new_course,
        )
        new_enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=new_broadcast, course=new_course, user=self.learner,
            status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(new_enrollment)
        new_course.refresh_from_db()
        self.assertIsNone(new_course.channel_id)


class EducationHierarchyDiscoveryAndDashboardTests(APITestCase):
    """Phase 2: learner-facing discovery + institution dashboard
    integration for the new hierarchy - exercised through the real
    public content-detail and dashboard API views, not helper functions
    directly."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900401', 'disco_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Discovery Academy')
        self.program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Discovery Program')
        self.institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, program=self.program, name='Discovery Class',
            status=EducationAcademicRecordStatus.PUBLISHED,
        )
        # Deliberately NOT setting course.program directly - only
        # institution_class - to prove the program page's course list
        # still finds it via the class relationship.
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, institution_class=self.institution_class, title='Discovery Course',
            status=EducationAcademicRecordStatus.PUBLISHED,
        )
        self.program_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.PROGRAM,
            program=self.program, title='Discovery Program', status=EducationBroadcastStatus.PUBLISHED,
        )
        self.course_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=self.course, title='Discovery Course', status=EducationBroadcastStatus.PUBLISHED,
        )

    def test_program_detail_lists_class_attached_course_and_its_classes(self):
        # _build_public_education_content_detail's payload nests under
        # "content" (see its return: {"content": detail_item, "progress":
        # ..., ...} — the frontend's useEducationCourseDetail hook reads
        # payload.content the same way).
        url = f'/api/v1/education/contents/{self.program_broadcast.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        content = response.data['content']
        course_ids = {row['id'] for row in content['courses']}
        self.assertIn(str(self.course.id), course_ids)
        class_ids = {row['id'] for row in content['classes']}
        self.assertIn(str(self.institution_class.id), class_ids)
        # The sibling course has its own published broadcast (set up in
        # setUp) - the program page must resolve that so a learner has
        # somewhere to actually navigate to view it.
        course_row = next(row for row in content['courses'] if row['id'] == str(self.course.id))
        self.assertEqual(course_row['broadcastId'], str(self.course_broadcast.id))

    def test_course_detail_exposes_its_class(self):
        url = f'/api/v1/education/contents/{self.course_broadcast.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        content = response.data['content']
        self.assertEqual(content['institutionClass']['id'], str(self.institution_class.id))
        self.assertEqual(content['institutionClass']['programId'], str(self.program.id))

    def test_program_page_course_list_is_not_stale_after_class_moves_to_another_program(self):
        other_program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Other Program')
        self.institution_class.program = other_program
        self.institution_class.save(update_fields=['program'])
        # course.program was never set directly, so this exercises the
        # institution_class__program half of the discovery query, not
        # just a fresh course.program value.
        other_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.PROGRAM,
            program=other_program, title='Other Program', status=EducationBroadcastStatus.PUBLISHED,
        )
        response = self.client.get(f'/api/v1/education/contents/{other_broadcast.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        course_ids = {row['id'] for row in response.data['content']['courses']}
        self.assertIn(str(self.course.id), course_ids)

    def test_dashboard_reports_program_and_class_counts(self):
        url = f'/api/v1/broadcasts/education/institutions/{self.institution.id}/dashboard/'
        self.client.force_authenticate(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['metrics']['program_count'], 1)
        self.assertEqual(response.data['metrics']['class_count'], 1)

    def test_institution_summary_reports_class_count(self):
        from apps.broadcasts.views import _build_public_institution_summary
        summary = _build_public_institution_summary(self.institution)
        self.assertEqual(summary['classCount'], 1)


class EducationHierarchyDeletedObjectBehaviorTests(APITestCase):
    """Task 7's 'Deleted/archived object behavior' - a Program or Class
    referenced by live Enrollment/StaffAssignment/Course rows can be
    deleted at any time (on_delete=SET_NULL throughout, no protection);
    the rest of the system must keep working against the orphaned rows,
    not crash."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900501', 'orphan_owner')
        self.learner = _make_user(User, '900502', 'orphan_learner')
        self.partner = Partner.objects.create(owner=self.owner, name='Orphan Partner', slug='orphan-partner-epc')
        self.institution = EducationInstitution.objects.create(
            owner=self.owner, name='Orphan Academy', partner=self.partner,
        )
        self.program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Orphan Program')
        self.institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, program=self.program, name='Orphan Class',
        )
        self.course = EducationInstitutionCourse.objects.create(
            institution=self.institution, institution_class=self.institution_class, title='Orphan Course',
            status=EducationAcademicRecordStatus.PUBLISHED,
        )
        self.broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.COURSE,
            course=self.course, title='Orphan Course', status=EducationBroadcastStatus.PUBLISHED,
        )
        self.enrollment = EducationInstitutionEnrollment.objects.create(
            institution=self.institution, broadcast=self.broadcast, program=self.program,
            institution_class=self.institution_class, course=self.course, user=self.learner,
            status=EducationEnrollmentStatus.ENROLLED,
        )
        sync_enrollment_communication(self.enrollment)

    def test_deleting_program_leaves_enrollment_and_course_serialization_intact(self):
        self.program.delete()
        self.enrollment.refresh_from_db()
        self.course.refresh_from_db()
        self.assertIsNone(self.enrollment.program_id)
        self.assertIsNone(self.course.institution_class.program_id if self.course.institution_class else None)
        serialized = EducationInstitutionEnrollmentSerializer(self.enrollment).data
        self.assertIsNone(serialized.get('program_id'))
        self.assertEqual(serialized['course_id'], str(self.course.id))

        url = f'/api/v1/education/contents/{self.broadcast.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_deleting_class_leaves_enrollment_sync_working(self):
        self.institution_class.delete()
        self.enrollment.refresh_from_db()
        self.course.refresh_from_db()
        self.assertIsNone(self.enrollment.institution_class_id)
        self.assertIsNone(self.course.institution_class_id)
        # sync must still run cleanly against the now-orphaned enrollment -
        # no institution_class to touch, everything else unaffected.
        sync_enrollment_communication(self.enrollment)
        self.enrollment.status = EducationEnrollmentStatus.CANCELLED
        self.enrollment.save(update_fields=['status'])
        sync_enrollment_communication(self.enrollment)

    def test_deleting_class_leaves_staff_assignment_revoke_working(self):
        membership = EducationInstitutionMembership.objects.create(
            institution=self.institution, user=self.learner,
            role=EducationInstitutionMembershipRole.LECTURER, status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        assignment = EducationInstitutionStaffAssignment.objects.create(
            institution=self.institution, membership=membership, institution_class=self.institution_class,
            role=EducationInstitutionStaffAssignmentRole.INSTRUCTOR, status=EducationInstitutionStaffAssignmentStatus.ACTIVE,
        )
        sync_staff_assignment_communication(assignment)
        self.institution_class.delete()
        assignment.refresh_from_db()
        self.assertIsNone(assignment.institution_class_id)
        # Must not raise even though the class it originally granted admin
        # on is gone.
        revoke_staff_assignment_communication(assignment)


class EducationProgramFullDetailTests(APITestCase):
    """Phase 3: the Program form is no longer name-only — every new field
    must actually round-trip through the real create/update API and
    persist in Postgres, not just exist as a frontend-only value."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900601', 'progdetail_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Full Detail Academy')

    def _programs_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/programs/'

    def _program_detail_url(self, program_id):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/programs/{program_id}/'

    def test_create_program_with_full_details_persists_every_field(self):
        self.client.force_authenticate(self.owner)
        body = {
            'title': 'Doctor of Veterinary Medicine',
            'code': 'DVM',
            'program_type': 'Degree',
            'summary': 'A five year professional program.',
            'description': 'Full description here.',
            'level': 'Professional',
            'duration_value': 5,
            'duration_unit': 'Years',
            'department': 'Veterinary Sciences',
            'faculty': 'Faculty of Veterinary Medicine',
            'start_date': '2027-09-01',
            'end_date': '2032-06-30',
            'entry_requirements': 'A-levels in Biology and Chemistry.',
            'target_audience': 'Aspiring veterinarians.',
            'learning_outcomes': ['Diagnose common animal diseases', 'Perform basic surgery'],
            'seat_limit': 60,
            'price_amount': 25000,
            'visibility': 'public',
            'status': 'published',
        }
        response = self.client.post(self._programs_url(), body, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        program_id = response.data['program']['id']

        program = EducationInstitutionProgram.objects.get(id=program_id)
        self.assertEqual(program.code, 'DVM')
        self.assertEqual(program.program_type, 'Degree')
        self.assertEqual(program.level, 'Professional')
        self.assertEqual(program.duration_value, 5)
        self.assertEqual(program.duration_unit, 'Years')
        self.assertEqual(program.department, 'Veterinary Sciences')
        self.assertEqual(program.faculty, 'Faculty of Veterinary Medicine')
        self.assertEqual(str(program.start_date), '2027-09-01')
        self.assertEqual(str(program.end_date), '2032-06-30')
        self.assertEqual(program.entry_requirements, 'A-levels in Biology and Chemistry.')
        self.assertEqual(program.target_audience, 'Aspiring veterinarians.')
        self.assertEqual(program.learning_outcomes, ['Diagnose common animal diseases', 'Perform basic surgery'])
        self.assertEqual(program.seat_limit, 60)
        self.assertEqual(float(program.price_amount), 25000.0)
        self.assertEqual(program.visibility, 'public')
        self.assertEqual(program.status, 'published')
        self.assertFalse(program.is_free)

        # And a fresh GET reflects the same persisted values - not just
        # what create() echoed back in the same request/response cycle.
        get_response = self.client.get(self._program_detail_url(program_id))
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        fetched = get_response.data['program']
        self.assertEqual(fetched['code'], 'DVM')
        self.assertEqual(fetched['duration_value'], 5)
        self.assertEqual(fetched['duration_unit'], 'Years')
        self.assertEqual(fetched['learning_outcomes'], ['Diagnose common animal diseases', 'Perform basic surgery'])

    def test_update_program_details_persists(self):
        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='MBA')
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            self._program_detail_url(program.id),
            {'program_type': 'Professional', 'level': 'Graduate', 'duration_value': 2, 'duration_unit': 'Years', 'seat_limit': 40},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        program.refresh_from_db()
        self.assertEqual(program.program_type, 'Professional')
        self.assertEqual(program.level, 'Graduate')
        self.assertEqual(program.duration_value, 2)
        self.assertEqual(program.seat_limit, 40)

    def test_program_dashboard_payload_has_classes_courses_and_metrics(self):
        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='BSc Computer Science')
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, program=program, name='Level 100')
        EducationInstitutionCourse.objects.create(institution=self.institution, program=program, title='Intro to Programming')
        EducationInstitutionCourse.objects.create(institution=self.institution, institution_class=institution_class, title='Discrete Math')
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._program_detail_url(program.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['metrics']['class_count'], 1)
        # Both the direct-program course AND the class-attached course
        # must show up — the same Q(program=X)|Q(institution_class__program=X)
        # fix applied to the public discovery payload in the previous
        # phase, verified here for the provider-side dashboard too.
        self.assertEqual(response.data['metrics']['course_count'], 2)
        self.assertEqual(len(response.data['classes']), 1)
        self.assertEqual(len(response.data['courses']), 2)

    def test_student_cannot_create_or_update_program_full_details(self):
        User = get_user_model()
        student = _make_user(User, '900602', 'progdetail_student')
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=student,
            role=EducationInstitutionMembershipRole.STUDENT, status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        self.client.force_authenticate(student)
        create_response = self.client.post(self._programs_url(), {'title': 'Illicit Program'}, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN, create_response.data)

        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Real Program')
        update_response = self.client.patch(self._program_detail_url(program.id), {'program_type': 'Degree'}, format='json')
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN, update_response.data)


class EducationClassFullDetailTests(APITestCase):
    """Same real-persistence standard as EducationProgramFullDetailTests,
    for Class."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900701', 'classdetail_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Class Detail Academy')

    def _classes_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/classes/'

    def _class_detail_url(self, class_id):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/classes/{class_id}/'

    def test_create_class_with_full_details_persists_every_field(self):
        self.client.force_authenticate(self.owner)
        body = {
            'name': 'DVM Class of 2029',
            'code': 'DVM-2029',
            'description': 'Cohort entering in 2027, graduating 2032.',
            'class_type': 'Cohort',
            'level': 'Year 1',
            'academic_year': '2027/2028',
            'term': 'Full Year',
            'start_date': '2027-09-01',
            'end_date': '2028-06-30',
            'seat_limit': 30,
            'visibility': 'public',
            'status': 'published',
        }
        response = self.client.post(self._classes_url(), body, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        class_id = response.data['class']['id']

        institution_class = EducationInstitutionClass.objects.get(id=class_id)
        self.assertEqual(institution_class.code, 'DVM-2029')
        self.assertEqual(institution_class.class_type, 'Cohort')
        self.assertEqual(institution_class.level, 'Year 1')
        self.assertEqual(institution_class.academic_year, '2027/2028')
        self.assertEqual(institution_class.term, 'Full Year')
        self.assertEqual(str(institution_class.start_date), '2027-09-01')
        self.assertEqual(str(institution_class.end_date), '2028-06-30')
        self.assertEqual(institution_class.seat_limit, 30)
        self.assertEqual(institution_class.visibility, 'public')
        self.assertEqual(institution_class.status, 'published')

        get_response = self.client.get(self._class_detail_url(class_id))
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertEqual(get_response.data['class']['code'], 'DVM-2029')
        self.assertEqual(get_response.data['class']['academic_year'], '2027/2028')

    def test_update_class_details_persists(self):
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Cohort A')
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            self._class_detail_url(institution_class.id),
            {'class_type': 'Cohort', 'level': 'Level 200', 'seat_limit': 25},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        institution_class.refresh_from_db()
        self.assertEqual(institution_class.class_type, 'Cohort')
        self.assertEqual(institution_class.level, 'Level 200')
        self.assertEqual(institution_class.seat_limit, 25)

    def test_class_dashboard_payload_has_courses_and_metrics(self):
        institution_class = EducationInstitutionClass.objects.create(institution=self.institution, name='Web Dev Bootcamp')
        EducationInstitutionCourse.objects.create(institution=self.institution, institution_class=institution_class, title='HTML & CSS')
        EducationInstitutionCourse.objects.create(institution=self.institution, institution_class=institution_class, title='JavaScript')
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._class_detail_url(institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['metrics']['course_count'], 2)
        self.assertEqual(len(response.data['courses']), 2)

    def test_class_dashboard_requires_manage_role_not_just_membership(self):
        User = get_user_model()
        student = _make_user(User, '900702', 'classdetail_student')
        EducationInstitutionMembership.objects.create(
            institution=self.institution, user=student,
            role=EducationInstitutionMembershipRole.STUDENT, status=EducationInstitutionMembershipStatus.ACTIVE,
        )
        institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, name='Published Class', status=EducationAcademicRecordStatus.PUBLISHED,
        )
        self.client.force_authenticate(student)
        response = self.client.get(self._class_detail_url(institution_class.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)


class EducationProgramBroadcastTests(APITestCase):
    """Section 13/14 of the spec: Program (and Class) broadcast controls,
    built on the existing Education broadcast/discovery architecture -
    not a new system."""

    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900801', 'progbroadcast_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Broadcast Academy')
        self.program = EducationInstitutionProgram.objects.create(institution=self.institution, title='PhD Theology')

    def _broadcasts_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/broadcasts/'

    def _broadcast_detail_url(self, broadcast_id):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/broadcasts/{broadcast_id}/'

    def _programs_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/programs/'

    def test_broadcasting_program_makes_it_discoverable(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._broadcasts_url(),
            {'program_id': str(self.program.id), 'broadcast_kind': 'program', 'status': 'published'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast_id = response.data['broadcast']['id']
        self.assertEqual(response.data['broadcast']['broadcast_kind'], 'program')
        self.assertEqual(response.data['broadcast']['program_id'], str(self.program.id))

        # Visible anonymously through the real public discovery endpoint -
        # not just present in the institution's own management list.
        detail = self.client.get(f'/api/v1/education/contents/{broadcast_id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        self.assertEqual(detail.data['content']['type'], 'program')
        self.assertEqual(detail.data['content']['title'], 'PhD Theology')

        # And the Programs list itself now reports it as broadcast.
        list_response = self.client.get(self._programs_url())
        self.assertEqual(list_response.status_code, status.HTTP_200_OK, list_response.data)
        row = next(p for p in list_response.data['programs'] if p['id'] == str(self.program.id))
        self.assertEqual(row['broadcast_id'], broadcast_id)

    def test_removing_program_from_broadcast_does_not_delete_program(self):
        self.client.force_authenticate(self.owner)
        create_response = self.client.post(
            self._broadcasts_url(),
            {'program_id': str(self.program.id), 'broadcast_kind': 'program', 'status': 'published'},
            format='json',
        )
        broadcast_id = create_response.data['broadcast']['id']

        remove_response = self.client.patch(self._broadcast_detail_url(broadcast_id), {'status': 'draft'}, format='json')
        self.assertEqual(remove_response.status_code, status.HTTP_200_OK, remove_response.data)

        # No longer publicly discoverable (content detail 404s - broadcast
        # exists but isn't status=published, which the public view
        # requires)...
        detail = self.client.get(f'/api/v1/education/contents/{broadcast_id}/')
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        # ...and the Programs list no longer reports it as broadcast...
        list_response = self.client.get(self._programs_url())
        row = next(p for p in list_response.data['programs'] if p['id'] == str(self.program.id))
        self.assertIsNone(row['broadcast_id'])
        # ...but the Program itself is completely untouched.
        self.assertTrue(EducationInstitutionProgram.objects.filter(id=self.program.id).exists())
        self.program.refresh_from_db()
        self.assertEqual(self.program.title, 'PhD Theology')


class EducationClassBroadcastTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = _make_user(User, '900901', 'classbroadcast_owner')
        self.institution = EducationInstitution.objects.create(owner=self.owner, name='Class Broadcast Academy')
        self.institution_class = EducationInstitutionClass.objects.create(
            institution=self.institution, name='Web Development Bootcamp',
            status=EducationAcademicRecordStatus.PUBLISHED,
        )

    def _broadcasts_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/broadcasts/'

    def _broadcast_detail_url(self, broadcast_id):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/broadcasts/{broadcast_id}/'

    def _classes_url(self):
        return f'/api/v1/broadcasts/education/institutions/{self.institution.id}/classes/'

    def test_broadcasting_class_makes_it_discoverable_as_class_type(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._broadcasts_url(),
            {'institution_class_id': str(self.institution_class.id), 'broadcast_kind': 'institution_class', 'status': 'published'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        broadcast_id = response.data['broadcast']['id']
        self.assertEqual(response.data['broadcast']['broadcast_kind'], 'institution_class')
        self.assertEqual(response.data['broadcast']['institution_class_id'], str(self.institution_class.id))

        detail = self.client.get(f'/api/v1/education/contents/{broadcast_id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        # Discovery type is specifically "class" - distinct from "program"
        # and "course" - this is the new EducationBroadcastKind.INSTITUTION_CLASS
        # kind, not a reuse of the pre-existing CLASS_SESSION kind.
        self.assertEqual(detail.data['content']['type'], 'class')
        self.assertEqual(detail.data['content']['title'], 'Web Development Bootcamp')
        self.assertIsNone(detail.data['content']['programId'])

        list_response = self.client.get(self._classes_url())
        row = next(c for c in list_response.data['classes'] if c['id'] == str(self.institution_class.id))
        self.assertEqual(row['broadcast_id'], broadcast_id)

    def test_class_broadcast_requires_a_class(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._broadcasts_url(),
            {'broadcast_kind': 'institution_class', 'status': 'published'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_removing_class_from_broadcast_does_not_delete_class(self):
        self.client.force_authenticate(self.owner)
        create_response = self.client.post(
            self._broadcasts_url(),
            {'institution_class_id': str(self.institution_class.id), 'broadcast_kind': 'institution_class', 'status': 'published'},
            format='json',
        )
        broadcast_id = create_response.data['broadcast']['id']

        remove_response = self.client.patch(self._broadcast_detail_url(broadcast_id), {'status': 'draft'}, format='json')
        self.assertEqual(remove_response.status_code, status.HTTP_200_OK, remove_response.data)

        detail = self.client.get(f'/api/v1/education/contents/{broadcast_id}/')
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(EducationInstitutionClass.objects.filter(id=self.institution_class.id).exists())
        self.institution_class.refresh_from_db()
        self.assertEqual(self.institution_class.name, 'Web Development Bootcamp')

    def test_program_broadcast_lists_its_broadcast_class(self):
        program = EducationInstitutionProgram.objects.create(institution=self.institution, title='Coding Academy Program')
        self.institution_class.program = program
        self.institution_class.save(update_fields=['program'])
        program_broadcast = EducationInstitutionBroadcast.objects.create(
            institution=self.institution, created_by=self.owner, broadcast_kind=EducationBroadcastKind.PROGRAM,
            program=program, title=program.title, status=EducationBroadcastStatus.PUBLISHED,
        )
        detail = self.client.get(f'/api/v1/education/contents/{program_broadcast.id}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        class_ids = {row['id'] for row in detail.data['content']['classes']}
        self.assertIn(str(self.institution_class.id), class_ids)
