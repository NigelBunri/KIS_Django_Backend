import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed Christian Community (CC) with large-scale demo data."

    def handle(self, *args, **options):
        from apps.partners.seed import ensure_kis_partner
        from apps.partners.models import (
            Partner,
            PartnerMembership,
            PartnerMembershipStatus,
            PartnerJobPost,
            PartnerApplication,
            PartnerApplicationStatus,
            PartnerPostComment,
            PartnerPostReaction,
            PartnerPolicy,
            PartnerRole,
            PartnerRoleAssignment,
            PartnerAuditEvent,
            PartnerAutomationRule,
            PartnerReportSnapshot,
            PartnerExportJob,
            PartnerExportStatus,
            PartnerExportSchedule,
            PartnerExportScheduleFrequency,
            PartnerAccessRequest,
            PartnerAccessRequestStatus,
            PartnerAccessReview,
            PartnerAccessReviewStatus,
            PartnerIntegration,
            PartnerWebhook,
            PartnerWebhookDelivery,
            PartnerWebhookDeliveryStatus,
            PartnerSetting,
            PartnerOrganizationProfile,
            PartnerFeatureFlag,
        )
        from apps.accounts.models import User
        from apps.chat.models import (
            Conversation,
            ConversationMember,
            ConversationSettings,
            ConversationType,
            BaseConversationRole,
        )
        from apps.communities.models import (
            Community,
            CommunityMembership,
            CommunityRole,
            CommunityPost,
        )
        from apps.groups.models import Group, GroupMembership, GroupRole
        from apps.channels.models import Channel
        from apps.channels.services import create_channel_with_conversation
        from apps.analytics.models import Metric, Dashboard, EventStream, EngagementScore
        from apps.partners.settings_catalog import PARTNER_SETTINGS_SECTIONS
        from apps.partners.services import log_partner_audit

        ensure_kis_partner()
        partner = Partner.objects.filter(slug__in=["cc", "kis"]).first()
        if not partner:
            self.stdout.write(self.style.ERROR("CC partner not found."))
            return

        seed_tag = "[CC-SEED]"
        now = timezone.now()

        def get_or_create_user(phone: str, name: str, title: str):
            user = User.objects.filter(phone=phone).first()
            if user:
                if user.display_name != name:
                    user.display_name = name
                    user.save(update_fields=["display_name", "updated_at"])
                return user
            user = User.objects.create_user(
                phone=phone,
                country="CM",
                email=f"{phone}@cc.example",
                username=phone.replace("+", ""),
                display_name=name,
            )
            return user

        def ensure_partner_member(user: User, role: str):
            PartnerMembership.objects.update_or_create(
                partner=partner,
                user=user,
                defaults={"status": PartnerMembershipStatus.MEMBER, "role": role},
            )
            if partner.main_conversation_id:
                ConversationMember.objects.get_or_create(
                    conversation_id=partner.main_conversation_id,
                    user=user,
                    defaults={"base_role": BaseConversationRole.MEMBER},
                )

        def ensure_conversation_role(user: User, base_role: str):
            if not partner.main_conversation_id:
                return
            ConversationMember.objects.update_or_create(
                conversation_id=partner.main_conversation_id,
                user=user,
                defaults={"base_role": base_role},
            )

        def create_community(owner: User, name: str, slug: str, description: str):
            community = Community.objects.filter(slug=slug).first()
            if community:
                return community

            main_conversation = Conversation.objects.create(
                type=ConversationType.GROUP,
                title=name,
                created_by=owner,
            )
            posts_conversation = Conversation.objects.create(
                type=ConversationType.POST,
                title=f"{name} posts",
                created_by=owner,
            )
            ConversationMember.objects.create(
                conversation=main_conversation,
                user=owner,
                base_role=BaseConversationRole.OWNER,
            )
            ConversationMember.objects.create(
                conversation=posts_conversation,
                user=owner,
                base_role=BaseConversationRole.OWNER,
            )
            ConversationSettings.objects.create(conversation=main_conversation)
            ConversationSettings.objects.create(conversation=posts_conversation)

            community = Community.objects.create(
                owner=owner,
                partner=partner,
                name=name,
                slug=slug,
                description=description,
                main_conversation=main_conversation,
                posts_conversation=posts_conversation,
            )
            CommunityMembership.objects.get_or_create(
                community=community,
                user=owner,
                role=CommunityRole.OWNER,
            )
            return community

        def create_group(owner: User, name: str, slug: str, community=None):
            group = Group.objects.filter(slug=slug, community=community).first()
            if group:
                return group
            conversation = Conversation.objects.create(
                type=ConversationType.GROUP,
                title=name,
                created_by=owner,
            )
            ConversationMember.objects.create(
                conversation=conversation,
                user=owner,
                base_role=BaseConversationRole.OWNER,
            )
            ConversationSettings.objects.create(conversation=conversation)
            group = Group.objects.create(
                owner=owner,
                partner=partner,
                community=community,
                name=name,
                slug=slug,
                conversation=conversation,
            )
            GroupMembership.objects.get_or_create(
                group=group,
                user=owner,
                role=GroupRole.OWNER,
            )
            return group

        def create_channel(owner: User, name: str, slug: str, community=None):
            channel = Channel.objects.filter(slug=slug, community=community).first()
            if channel:
                return channel
            return create_channel_with_conversation(
                owner=owner,
                name=name,
                slug=slug,
                description=f"{name} updates",
                partner=partner,
                community=community,
            )

        # ------------------------------------------------------------------
        # Leadership
        # ------------------------------------------------------------------
        leadership = [
            ("+10000000001", "General Overseer", "GO"),
            ("+10000000002", "Chief Executive Officer", "CEO"),
            ("+10000000003", "Director, Shekinah Global", "Director"),
            ("+10000000004", "Chief Technology Officer", "CTO"),
            ("+10000000005", "Chief Financial Officer", "CFO"),
            ("+10000000006", "Chief Communications Officer", "CCO"),
            ("+10000000007", "Chief Marketing Officer", "CMO"),
            ("+10000000008", "Chief Strategy Officer", "CSO"),
            ("+10000000009", "Chief Resource Officer", "CRO-Resources"),
            ("+10000000010", "Chief Project Officer", "CPO"),
            ("+10000000011", "Chief Risk & Assurance Officer", "CRAO"),
            ("+10000000012", "Chief Operating Officer", "COO"),
            ("+10000000013", "Chief Legal Officer", "CLO"),
        ]
        leaders = {}
        for phone, title, role in leadership:
            user = get_or_create_user(phone, title, role)
            leaders[role] = user
            ensure_partner_member(user, role="executive")

        # Ensure the CC governing body shows up as admins.
        ensure_conversation_role(leaders["GO"], BaseConversationRole.OWNER)
        for role_key in [
            "CEO",
            "Director",
            "CTO",
            "CFO",
            "CCO",
            "CMO",
            "CSO",
            "CRO-Resources",
            "CPO",
            "CRAO",
            "COO",
            "CLO",
        ]:
            ensure_conversation_role(leaders[role_key], BaseConversationRole.ADMIN)

        # ------------------------------------------------------------------
        # Branch communities
        # ------------------------------------------------------------------
        kiv = create_community(
            leaders["CEO"],
            "Kingdom Impact Ventures (KIV)",
            "kiv",
            "Corporate and organizational arm of CC.",
        )
        shekinah = create_community(
            leaders["Director"],
            "Shekinah Global",
            "shekinah-global",
            "Global ministry and outreach arm of CC.",
        )
        kis = create_community(
            leaders["CEO"],
            "Kingdom Impact Social (KIS)",
            "kis-social",
            "Social engagement and community programs.",
        )
        kim = create_community(
            leaders["CEO"],
            "Kingdom Impact Market (KIM)",
            "kim-market",
            "Marketplace and commerce initiatives.",
        )
        kip = create_community(
            leaders["CEO"],
            "Kingdom Impact Pay (KIP)",
            "kip-pay",
            "Payments and fintech services.",
        )
        kie = create_community(
            leaders["CEO"],
            "Kingdom Impact Education (KIE)",
            "kie-education",
            "Education and learning programs.",
        )
        kih = create_community(
            leaders["CEO"],
            "Kingdom Impact Health (KIH)",
            "kih-health",
            "Health and wellbeing programs.",
        )

        # ------------------------------------------------------------------
        # Channels
        # ------------------------------------------------------------------
        create_channel(leaders["GO"], "CC Announcements", "cc-announcements")
        create_channel(leaders["GO"], "CC Global Broadcast", "cc-global-broadcast")
        create_channel(leaders["CEO"], "CC Talent Hub", "cc-talent-hub")
        create_channel(leaders["COO"], "CC Ops Pulse", "cc-ops-pulse")
        create_channel(leaders["CEO"], "KIV Leadership", "kiv-leadership", kiv)
        create_channel(leaders["Director"], "Shekinah Global Updates", "shekinah-updates", shekinah)
        create_channel(leaders["CEO"], "KIS Social Broadcast", "kis-broadcast", kis)
        create_channel(leaders["CEO"], "KIM Market Ops", "kim-ops", kim)
        create_channel(leaders["CEO"], "KIP Payments Ops", "kip-ops", kip)
        create_channel(leaders["CEO"], "KIE Education Programs", "kie-programs", kie)
        create_channel(leaders["CEO"], "KIH Health Missions", "kih-missions", kih)

        # ------------------------------------------------------------------
        # Department groups
        # ------------------------------------------------------------------
        create_group(leaders["GO"], "Executive Council", "cc-executive-council")
        create_group(leaders["COO"], "Global Operations Hub", "cc-ops-hub")
        create_group(leaders["CTO"], "Innovation Lab", "cc-innovation-lab")
        create_group(leaders["CFO"], "Finance Strategy Circle", "cc-finance-circle")
        create_group(leaders["CCO"], "Communications Studio", "cc-comms-studio")
        departments = [
            ("engineering", "Engineering Guild"),
            ("finance", "Finance Office"),
            ("operations", "Operations Command"),
            ("people", "People & Culture"),
            ("marketing", "Marketing Studio"),
            ("security", "Security & Risk"),
        ]
        for code, name in departments:
            create_group(leaders["COO"], f"{name} (KIV)", f"kiv-{code}", kiv)
            create_group(leaders["Director"], f"{name} (Shekinah)", f"shek-{code}", shekinah)

        # ------------------------------------------------------------------
        # Managers, leads, teams
        # ------------------------------------------------------------------
        manager_roles = []
        for idx, role in enumerate(["CTO", "CFO", "CCO", "CMO", "CSO", "CRO-Resources", "CPO", "CRAO", "COO", "CLO"], start=1):
            manager = get_or_create_user(
                phone=f"+100000001{idx:02d}",
                name=f"{role} Manager",
                title="Manager",
            )
            ensure_partner_member(manager, role="manager")
            manager_roles.append(manager)
            lead_a = get_or_create_user(
                phone=f"+100000002{idx:02d}",
                name=f"{role} Team Lead A",
                title="Team Lead",
            )
            lead_b = get_or_create_user(
                phone=f"+100000003{idx:02d}",
                name=f"{role} Team Lead B",
                title="Team Lead",
            )
            ensure_partner_member(lead_a, role="lead")
            ensure_partner_member(lead_b, role="lead")

        # ------------------------------------------------------------------
        # General members pool
        # ------------------------------------------------------------------
        members = []
        for idx in range(1, 31):
            member = get_or_create_user(
                phone=f"+12223330{idx:02d}",
                name=f"CC Member {idx}",
                title="Staff",
            )
            ensure_partner_member(member, role="member")
            members.append(member)

        # ------------------------------------------------------------------
        # Org profile + policy
        # ------------------------------------------------------------------
        PartnerOrganizationProfile.objects.update_or_create(
            partner=partner,
            defaults={
                "display_name": "Christian Community (CC)",
                "legal_name": "Christian Community Global Organization",
                "tagline": "Faith-driven innovation across missions and enterprise.",
                "mission": "Empower communities through technology, discipleship, and sustainable ventures.",
                "vision": "A global ecosystem where every community thrives in purpose.",
                "website": "https://christiancommunity.example",
                "email": "contact@christiancommunity.example",
                "phone": "+1 555-000-0000",
                "industry": "Faith-based nonprofit + enterprise",
                "size": "10,000+",
                "founded_year": 1998,
                "headquarters": "Global HQ - Nairobi / Dallas / London",
                "logo_url": "https://dummyimage.com/512x512/2c3e50/ffffff.png&text=CC",
                "brand_colors": ["#1E3A8A", "#F59E0B", "#10B981"],
                "social_links": {
                    "linkedin": "https://linkedin.com/company/christian-community",
                    "x": "https://x.com/ccglobal",
                    "youtube": "https://youtube.com/@ccglobal",
                },
                "public_fields": {
                    "display_name": True,
                    "tagline": True,
                    "mission": True,
                    "vision": True,
                    "website": True,
                    "industry": True,
                    "size": True,
                    "headquarters": True,
                    "logo_url": True,
                    "brand_colors": True,
                    "social_linkedin": True,
                    "social_twitter": True,
                    "social_youtube": True,
                },
                "updated_by": leaders["GO"],
            },
        )

        policy, _ = PartnerPolicy.objects.get_or_create(partner=partner)
        policy.settings.update(
            {
                "security": {"require_mfa": True, "session_timeout_minutes": 30},
                "compliance": {"audit_enabled": True, "retention_days": 365},
                "retention": {"message_retention_days": 365, "broadcast_retention_days": 10},
                "dlp": {
                    "enabled": True,
                    "block_patterns": ["confidential", "NDA", "/\\bssn\\b/"],
                    "warn_patterns": ["internal-only", "/\\bpii\\b/"],
                },
                "integrations": {"sso_required": True, "scim_enabled": True},
            }
        )
        policy.save(update_fields=["settings", "updated_at"])

        # ------------------------------------------------------------------
        # Role assignments
        # ------------------------------------------------------------------
        role_owner = PartnerRole.objects.filter(partner=partner, name__iexact="Owner").first()
        role_admin = PartnerRole.objects.filter(partner=partner, name__iexact="Admin").first()
        role_manager = PartnerRole.objects.filter(partner=partner, name__iexact="Manager").first()
        role_analyst = PartnerRole.objects.filter(partner=partner, name__iexact="Analyst").first()
        if role_owner:
            PartnerRoleAssignment.objects.get_or_create(
                partner=partner,
                user=leaders["GO"],
                role=role_owner,
                scope_type="global",
            )
        if role_admin:
            for role_key in ["CEO", "Director", "COO", "CTO", "CFO", "CCO", "CMO"]:
                PartnerRoleAssignment.objects.get_or_create(
                    partner=partner,
                    user=leaders[role_key],
                    role=role_admin,
                    scope_type="global",
                )
        if role_manager:
            for manager in manager_roles:
                PartnerRoleAssignment.objects.get_or_create(
                    partner=partner,
                    user=manager,
                    role=role_manager,
                    scope_type="global",
                )
        if role_analyst and members:
            PartnerRoleAssignment.objects.get_or_create(
                partner=partner,
                user=members[0],
                role=role_analyst,
                scope_type="global",
            )

        # ------------------------------------------------------------------
        # Job posts + applications
        # ------------------------------------------------------------------
        if not PartnerJobPost.objects.filter(partner=partner, title__icontains="Global Talent").exists():
            job_posts = [
                PartnerJobPost.objects.create(
                    partner=partner,
                    title="Global Talent Pipeline",
                    description="Join CC global staff and mission teams.",
                    requirements="Leadership experience, faith-based mission alignment.",
                    steps=["Profile review", "Screening call", "Panel interview", "Offer", "Onboarding"],
                    auto_assign={
                        "communities": [str(kiv.id), str(shekinah.id)],
                    },
                ),
                PartnerJobPost.objects.create(
                    partner=partner,
                    title="Platform Engineer (KIP)",
                    description="Build payment infrastructure and APIs.",
                    requirements="Python, Django, payments or fintech background.",
                    steps=["Coding assessment", "Technical interview", "Team fit", "Offer"],
                    auto_assign={
                        "communities": [str(kip.id)],
                    },
                ),
                PartnerJobPost.objects.create(
                    partner=partner,
                    title="Community Program Lead (KIS)",
                    description="Lead community engagement initiatives.",
                    requirements="Community leadership and program delivery.",
                    steps=["Application review", "Interview", "Field trial", "Offer"],
                    auto_assign={
                        "communities": [str(kis.id)],
                    },
                ),
            ]
            for idx, job in enumerate(job_posts, start=1):
                applicant = get_or_create_user(
                    phone=f"+199999900{idx}",
                    name=f"Applicant {idx}",
                    title="Applicant",
                )
                PartnerApplication.objects.create(
                    partner=partner,
                    job_post=job,
                    user=applicant,
                    method="application",
                    message=f"{seed_tag} Excited to join {job.title}.",
                    answers={"desired_role": job.title},
                    status=PartnerApplicationStatus.PENDING,
                    profile_visible=True,
                    stage_index=0,
                    stage_state={"steps": job.steps, "current": 0},
                )

        # ------------------------------------------------------------------
        # Partner posts
        # ------------------------------------------------------------------
        if not partner.posts.filter(text__contains=seed_tag).exists():
            posts = [
                ("CC Global vision for 2026 launch.", leaders["GO"]),
                ("KIV quarterly roadmap released.", leaders["CEO"]),
                ("Shekinah Global prayer initiative starts next week.", leaders["Director"]),
                ("New cross-branch leadership sync next Monday.", leaders["COO"]),
                ("Global leadership townhall highlights.", leaders["CCO"]),
                ("Marketplace partnerships milestone.", leaders["CMO"]),
            ]
            created_posts = []
            for idx, (text, author) in enumerate(posts):
                created_posts.append(
                    partner.posts.create(
                        author=author,
                        text=f"{seed_tag} {text}",
                        is_broadcast=True,
                        created_at=now - datetime.timedelta(days=14 - idx),
                    )
                )
            for idx in range(18):
                created_posts.append(
                    partner.posts.create(
                        author=leaders["CTO"],
                        text=f"{seed_tag} Engineering update #{idx + 1}.",
                        created_at=now - datetime.timedelta(days=idx),
                    )
                )

            for post in created_posts:
                for commenter in members[:5]:
                    PartnerPostComment.objects.create(
                        post=post,
                        author=commenter,
                        text=f"{seed_tag} Insight from {commenter.display_name}.",
                        created_at=post.created_at + datetime.timedelta(hours=2),
                    )
                for reactor in members[5:12]:
                    PartnerPostReaction.objects.create(
                        post=post,
                        user=reactor,
                        emoji="🔥",
                        created_at=post.created_at + datetime.timedelta(hours=3),
                    )

        # ------------------------------------------------------------------
        # Community posts
        # ------------------------------------------------------------------
        for community in [kiv, shekinah, kis, kim, kip, kie, kih]:
            if community.posts.filter(text__contains=seed_tag).exists():
                continue
            for idx in range(4):
                CommunityPost.objects.create(
                    community=community,
                    author=leaders["COO"],
                    text=f"{seed_tag} {community.name} update #{idx + 1}.",
                    status="published",
                    created_at=now - datetime.timedelta(days=idx + 3),
                )

        # ------------------------------------------------------------------
        # Analytics
        # ------------------------------------------------------------------
        if not Metric.objects.filter(name__startswith="cc_metric_").exists():
            for idx in range(20):
                Metric.objects.create(
                    kind="partner",
                    name=f"cc_metric_{idx}",
                    value=100 + idx * 3,
                    unit="pts",
                    tags={"partner": str(partner.id), "segment": "global"},
                )

        if not Dashboard.objects.filter(name="CC Executive Dashboard").exists():
            Dashboard.objects.create(
                partner_id=partner.id,
                name="CC Executive Dashboard",
                definition={"widgets": ["engagement", "growth", "missions", "finance"]},
                is_shared=True,
            )

        if not EventStream.objects.filter(event_type="partner.seed").exists():
            EventStream.objects.create(
                event_type="partner.seed",
                payload={"partner": str(partner.id), "tag": seed_tag},
            )

        if not EngagementScore.objects.filter(
            target_id=partner.id, score_type="partner_health"
        ).exists():
            EngagementScore.objects.create(
                target_id=partner.id,
                score_type="partner_health",
                value=87.5,
                metadata={"seed": True},
            )

        # ------------------------------------------------------------------
        # Settings configs
        # ------------------------------------------------------------------
        for section in PARTNER_SETTINGS_SECTIONS:
            for feature in section.get("features", []):
                key = feature.get("key")
                if not key:
                    continue
                defaults = {
                    "note": f"{seed_tag} {feature.get('title', key)} configured.",
                    "owner": partner.name,
                }
                if key in ["content_rules", "membership_rules"]:
                    defaults = {
                        "approval_required": True,
                        "moderators": [leaders["COO"].display_name],
                        "limits": {"posts_per_day": 5, "attachments_per_post": 8},
                    }
                if key in ["org_locations", "units_departments"]:
                    defaults = {
                        "regions": ["Africa", "North America", "Europe"],
                        "departments": ["Ops", "Finance", "Engineering", "People"],
                    }
                PartnerSetting.objects.update_or_create(
                    partner=partner,
                    key=key,
                    defaults={"config": defaults, "updated_by": leaders["GO"]},
                )

        # ------------------------------------------------------------------
        # Integrations + webhooks
        # ------------------------------------------------------------------
        PartnerIntegration.objects.update_or_create(
            partner=partner,
            kind=PartnerIntegration.KIND_SSO,
            defaults={
                "provider": "Okta",
                "config": {
                    "allowed_domains": ["ccglobal.org"],
                    "metadata_url": "https://ccglobal.okta.com/metadata",
                    "login_url": "https://ccglobal.okta.com/login",
                },
                "is_enabled": True,
            },
        )
        PartnerIntegration.objects.update_or_create(
            partner=partner,
            kind=PartnerIntegration.KIND_SCIM,
            defaults={
                "provider": "Azure AD",
                "config": {"base_url": "https://scim.ccglobal.org/v2", "token": "seed-token"},
                "is_enabled": True,
            },
        )
        webhook, _ = PartnerWebhook.objects.get_or_create(
            partner=partner,
            name="CC Ops Webhook",
            defaults={
                "url": "https://hooks.example.org/cc/ops",
                "events": ["member.joined", "partner.post.created", "partner.audit"],
                "secret": "cc-secret",
                "is_active": True,
            },
        )
        PartnerWebhookDelivery.objects.get_or_create(
            webhook=webhook,
            event="partner.post.created",
            defaults={
                "payload": {"post": "seed"},
                "status": PartnerWebhookDeliveryStatus.DELIVERED,
                "response_code": 200,
                "attempt_count": 1,
            },
        )
        PartnerWebhookDelivery.objects.get_or_create(
            webhook=webhook,
            event="partner.audit",
            defaults={
                "payload": {"audit": "seed"},
                "status": PartnerWebhookDeliveryStatus.FAILED,
                "error_message": "Timeout",
                "attempt_count": 2,
            },
        )

        # ------------------------------------------------------------------
        # Automation rules
        # ------------------------------------------------------------------
        PartnerAutomationRule.objects.get_or_create(
            partner=partner,
            name="Auto-assign onboarding",
            defaults={
                "description": "Assign members to onboarding group.",
                "trigger": "member.joined",
                "conditions": {"status": "active"},
                "actions": [
                    {"type": "assign_role", "params": {"role": "Member"}},
                    {"type": "log_audit", "params": {"action": "onboarding.auto_assign"}},
                ],
                "created_by": leaders["GO"],
            },
        )
        PartnerAutomationRule.objects.get_or_create(
            partner=partner,
            name="Broadcast high priority",
            defaults={
                "description": "Trigger webhook for broadcast posts.",
                "trigger": "partner.post.created",
                "conditions": {"is_broadcast": True},
                "actions": [
                    {"type": "dispatch_webhook", "params": {"event": "broadcast.created"}},
                ],
                "created_by": leaders["COO"],
            },
        )

        # ------------------------------------------------------------------
        # Reports, exports, schedules
        # ------------------------------------------------------------------
        PartnerReportSnapshot.objects.get_or_create(
            partner=partner,
            kind="engagement",
            defaults={
                "data": {
                    "weekly_active": 4200,
                    "reaction_rate": 0.68,
                    "top_communities": ["KIV", "KIS", "KIE"],
                },
                "created_by": leaders["CMO"],
            },
        )
        PartnerExportSchedule.objects.get_or_create(
            partner=partner,
            kind="summary",
            defaults={
                "export_format": "csv",
                "frequency": PartnerExportScheduleFrequency.WEEKLY,
                "is_active": True,
                "created_by": leaders["CFO"],
                "next_run_at": now + datetime.timedelta(days=7),
            },
        )
        PartnerExportJob.objects.get_or_create(
            partner=partner,
            kind="summary",
            defaults={
                "export_format": "csv",
                "status": PartnerExportStatus.COMPLETED,
                "file_path": "exports/cc/summary.csv",
                "metadata": {"seed": True},
                "created_by": leaders["CFO"],
                "finished_at": now - datetime.timedelta(hours=1),
            },
        )

        # ------------------------------------------------------------------
        # Access requests & reviews
        # ------------------------------------------------------------------
        if members:
            access_request, _ = PartnerAccessRequest.objects.get_or_create(
                partner=partner,
                requester=members[1],
                defaults={
                    "target_user": members[2],
                    "requested_role": role_manager,
                    "scope_type": "community",
                    "scope_id": str(kiv.id),
                    "justification": "Needs access to lead KIV growth squad.",
                    "status": PartnerAccessRequestStatus.PENDING,
                },
            )
            if access_request.status == PartnerAccessRequestStatus.PENDING:
                access_request.decided_by = leaders["COO"]
                access_request.status = PartnerAccessRequestStatus.APPROVED
                access_request.decided_at = now - datetime.timedelta(days=2)
                access_request.save(update_fields=["decided_by", "status", "decided_at"])

        PartnerAccessReview.objects.get_or_create(
            partner=partner,
            name="Q1 Access Review",
            defaults={
                "scope_type": "global",
                "findings": {
                    "over_privileged": 3,
                    "remediations": ["Remove admin from temp contractors"],
                },
                "status": PartnerAccessReviewStatus.OPEN,
                "created_by": leaders["CRO-Resources"],
            },
        )

        # ------------------------------------------------------------------
        # Feature flags
        # ------------------------------------------------------------------
        PartnerFeatureFlag.objects.update_or_create(
            partner=partner,
            key="broadcast_center",
            defaults={"is_enabled": True},
        )
        PartnerFeatureFlag.objects.update_or_create(
            partner=partner,
            key="leadership_scorecards",
            defaults={"is_enabled": True},
        )

        # ------------------------------------------------------------------
        # Audit events
        # ------------------------------------------------------------------
        if not PartnerAuditEvent.objects.filter(
            partner=partner,
            action__startswith="seed.",
        ).exists():
            log_partner_audit(
                partner=partner,
                actor=leaders["GO"],
                action="seed.initialized",
                target_type="partner",
                target_id=str(partner.id),
                metadata={"tag": seed_tag},
            )
            log_partner_audit(
                partner=partner,
                actor=leaders["COO"],
                action="seed.access_review",
                target_type="access_review",
                target_id="Q1",
                metadata={"status": "open"},
            )

        self.stdout.write(self.style.SUCCESS("CC global company demo data seeded."))
