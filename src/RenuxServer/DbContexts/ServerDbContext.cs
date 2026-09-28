using Microsoft.EntityFrameworkCore;
using RenuxServer.Models;

namespace RenuxServer.DbContexts;

public class ServerDbContext : DbContext
{
    public ServerDbContext(DbContextOptions<ServerDbContext> options) : base(options) { }

    public DbSet<User> Users { get; set; }
    public DbSet<ActiveChat> Chats { get; set; }
    public DbSet<ChatMessage> ChatMessages { get; set; }
    public DbSet<Organization> Organizations { get; set; }
    public DbSet<Major> Majors { get; set; }
    public DbSet<Role> Roles { get; set; }
    public DbSet<GuestChat> GuestChats { get; set; }
    public DbSet<CouncilSignupRequest> CouncilSignupRequests { get; set; }
    public DbSet<NotificationPreference> NotificationPreferences { get; set; }
    public DbSet<UserNotification> UserNotifications { get; set; }
    public DbSet<ProductEvent> ProductEvents { get; set; }

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        var users = modelBuilder.Entity<User>();
        var activeChats = modelBuilder.Entity<ActiveChat>();
        var message = modelBuilder.Entity<ChatMessage>();
        var org = modelBuilder.Entity<Organization>();
        var majors = modelBuilder.Entity<Major>();
        var role = modelBuilder.Entity<Role>();
        var guest = modelBuilder.Entity<GuestChat>();
        var councilSignup = modelBuilder.Entity<CouncilSignupRequest>();
        var notificationPreference = modelBuilder.Entity<NotificationPreference>();
        var userNotification = modelBuilder.Entity<UserNotification>();
        var productEvent = modelBuilder.Entity<ProductEvent>();

        users.ToTable("users").HasIndex(p => p.UserId).IsUnique();
        activeChats.ToTable("active_chats");
        message.ToTable("chat_messages").HasIndex(c => c.CreatedTime);
        message.HasIndex(c => new { c.ParentQuestionId, c.AnswerVersion })
            .IsUnique()
            .HasFilter("parent_question_id IS NOT NULL AND answer_version IS NOT NULL AND is_ask = FALSE");
        message.HasIndex(c => c.ParentQuestionId)
            .IsUnique()
            .HasFilter("parent_question_id IS NOT NULL AND is_ask = FALSE AND is_current = TRUE");
        message.HasIndex(c => c.RequestId)
            .IsUnique()
            .HasFilter("request_id IS NOT NULL");
        message.HasOne(c => c.ParentQuestion)
            .WithMany()
            .HasForeignKey(c => c.ParentQuestionId)
            .OnDelete(DeleteBehavior.SetNull);
        majors.ToTable("majors").HasIndex(m => m.Majorname).IsUnique();
        org.ToTable("organizations").HasIndex(o => o.MajorId).IsUnique();
        majors.HasData([
            new() { Id=new("293e8c9e-5c1d-40d7-adf4-3df7a419e4d6"), Majorname="통계학과" },
            new(){Id=new("f762ae12-21f7-4943-a78d-ab3931506306"), Majorname="수학과"}
            ]);
        role.ToTable("roles").HasIndex(r => r.Rolename).IsUnique();
        role.HasData([
            new() { Id = new("c22bc8f7-98b8-45a3-9053-3b779e027649"), Rolename = "학생회" },
            new() { Id = new("ec62f7d6-069d-4a47-8801-db61b938a299"), Rolename = "관리자" },
            new() { Id = new("b4114fd1-c9f0-4171-821f-b53a15faba9b"), Rolename = "일반학생" },
            // 어드민 인가 허용목록(AdminProxyApis)·프론트 가드에서 쓰는 역할 — seed 누락 보완
            new() { Id = new("7a3f2c44-9d1e-4b6a-8f25-6c0e9b51d7a2"), Rolename = "총학생회" }
            ]);

        guest.ToTable("guest");
        councilSignup.ToTable("council_signup_requests");
        councilSignup.HasIndex(r => r.UserId);
        councilSignup.HasIndex(r => r.Status);
        councilSignup.HasOne(r => r.Major).WithMany().HasForeignKey(r => r.MajorId);
        notificationPreference.ToTable("notification_preferences");
        notificationPreference.HasIndex(p => new { p.UserId, p.Topic }).IsUnique();
        notificationPreference.HasOne(p => p.User).WithMany().HasForeignKey(p => p.UserId);
        userNotification.ToTable("user_notifications");
        userNotification.HasIndex(n => n.UserId);
        userNotification.HasIndex(n => n.TargetDate);
        userNotification.HasIndex(n => n.DedupKey).IsUnique();
        userNotification.HasOne(n => n.User).WithMany().HasForeignKey(n => n.UserId);
        productEvent.ToTable("product_events");
        productEvent.HasIndex(e => e.IdempotencyKey).IsUnique();
        productEvent.HasIndex(e => new { e.EventType, e.OccurredTime });
        productEvent.HasIndex(e => new { e.SubjectKey, e.OccurredTime });
        productEvent.HasIndex(e => new { e.AnswerKey, e.EventType });

        users.Property(u => u.Id).HasColumnName("id");
        users.Property(u => u.UserId).HasColumnName("user_id");
        users.Property(u => u.HashPassword).HasColumnName("password");
        users.Property(u => u.Username).HasColumnName("user_name");
        users.Property(u => u.MajorId).HasColumnName("major_id");
        users.Property(u => u.RoleId).HasColumnName("role");
        users.Property(u => u.CreatedTime).HasColumnName("created_time");
        users.Property(u => u.UpdatedTime).HasColumnName("updated_time");

        activeChats.Property(c => c.Id).HasColumnName("id");
        activeChats.Property(c => c.UserId).HasColumnName("user_id");
        activeChats.Property(c => c.OrganizationId).HasColumnName("organization_id");
        activeChats.Property(c => c.Title).HasColumnName("title");
        activeChats.Property(c => c.CreatedTime).HasColumnName("created_time");
        activeChats.Property(c => c.UpdatedTime).HasColumnName("updated_time");

        message.Property(c => c.Id).HasColumnName("id");
        message.Property(c => c.ChatId).HasColumnName("chat_id");
        message.Property(c => c.IsAsk).HasColumnName("is_ask");
        message.Property(c => c.Content).HasColumnName("content");
        message.Property(c => c.CreatedTime).HasColumnName("created_time");
        message.Property(c => c.ParentQuestionId).HasColumnName("parent_question_id");
        message.Property(c => c.AnswerVersion).HasColumnName("answer_version");
        message.Property(c => c.VersionCreatedTime).HasColumnName("version_created_time");
        message.Property(c => c.IsCurrent).HasColumnName("is_current").HasDefaultValue(true);
        message.Property(c => c.SourcesJson).HasColumnName("sources_json");
        message.Property(c => c.RequestId).HasColumnName("request_id");
        message.Property(c => c.SuggestedQuestionsJson).HasColumnName("suggested_questions_json");
        message.Property(c => c.Grounded).HasColumnName("grounded");
        message.Property(c => c.GroundingScore).HasColumnName("grounding_score");
        message.Property(c => c.VerificationStatus).HasColumnName("verification_status");
        message.Property(c => c.RelevanceScore).HasColumnName("relevance_score");
        message.Property(c => c.RetrievalMode).HasColumnName("retrieval_mode");
        message.Property(c => c.DegradedDatasetsJson).HasColumnName("degraded_datasets_json");
        message.Property(c => c.IsFallback).HasColumnName("is_fallback").HasDefaultValue(false);
        message.Property(c => c.FallbackReason).HasColumnName("fallback_reason");

        org.Property(o => o.Id).HasColumnName("id");
        org.Property(o => o.MajorId).HasColumnName("major_id");
        org.Property(o => o.IsActive).HasColumnName("is_active");
        org.Property(o => o.CreatedTime).HasColumnName("created_time");
        org.Property(o => o.UpdatedTime).HasColumnName("updated_time");

        majors.Property(d => d.Id).HasColumnName("id");
        majors.Property(d => d.Majorname).HasColumnName("major_name");

        role.Property(r => r.Id).HasColumnName("id");
        role.Property(r => r.Rolename).HasColumnName("role_name");

        guest.Property(g => g.Id).HasColumnName("id");
        guest.Property(g => g.OrganizationId).HasColumnName("organization_id");
        guest.Property(g => g.Title).HasColumnName("title");
        guest.Property(g => g.CreatedTime).HasColumnName("created_time");
        guest.Property(g => g.UpdatedTime).HasColumnName("updated_time");

        councilSignup.Property(r => r.Id).HasColumnName("id");
        councilSignup.Property(r => r.UserId).HasColumnName("user_id");
        councilSignup.Property(r => r.HashPassword).HasColumnName("password");
        councilSignup.Property(r => r.Username).HasColumnName("user_name");
        councilSignup.Property(r => r.MajorId).HasColumnName("major_id");
        councilSignup.Property(r => r.Status).HasColumnName("status").HasDefaultValue("pending");
        councilSignup.Property(r => r.CreatedTime).HasColumnName("created_time");
        councilSignup.Property(r => r.ReviewedTime).HasColumnName("reviewed_time");
        councilSignup.Property(r => r.ReviewedByUserId).HasColumnName("reviewed_by_user_id");
        councilSignup.Property(r => r.ReviewNote).HasColumnName("review_note");

        notificationPreference.Property(p => p.Id).HasColumnName("id");
        notificationPreference.Property(p => p.UserId).HasColumnName("user_id");
        notificationPreference.Property(p => p.Topic).HasColumnName("topic");
        notificationPreference.Property(p => p.Enabled).HasColumnName("enabled").HasDefaultValue(false);
        notificationPreference.Property(p => p.RemindDaysBefore).HasColumnName("remind_days_before").HasDefaultValue("7,1,0");
        notificationPreference.Property(p => p.Channel).HasColumnName("channel").HasDefaultValue("in_app");
        notificationPreference.Property(p => p.CreatedTime).HasColumnName("created_time");
        notificationPreference.Property(p => p.UpdatedTime).HasColumnName("updated_time");

        userNotification.Property(n => n.Id).HasColumnName("id");
        userNotification.Property(n => n.UserId).HasColumnName("user_id");
        userNotification.Property(n => n.Topic).HasColumnName("topic");
        userNotification.Property(n => n.Source).HasColumnName("source");
        userNotification.Property(n => n.SourceId).HasColumnName("source_id");
        userNotification.Property(n => n.DedupKey).HasColumnName("dedup_key");
        userNotification.Property(n => n.Title).HasColumnName("title");
        userNotification.Property(n => n.Body).HasColumnName("body");
        userNotification.Property(n => n.TargetDate).HasColumnName("target_date");
        userNotification.Property(n => n.ReminderDate).HasColumnName("reminder_date");
        userNotification.Property(n => n.ReminderDaysBefore).HasColumnName("reminder_days_before");
        userNotification.Property(n => n.Url).HasColumnName("url");
        userNotification.Property(n => n.IsRead).HasColumnName("is_read").HasDefaultValue(false);
        userNotification.Property(n => n.CreatedTime).HasColumnName("created_time");
        userNotification.Property(n => n.ReadTime).HasColumnName("read_time");

        productEvent.Property(e => e.Id).HasColumnName("id");
        productEvent.Property(e => e.EventType).HasColumnName("event_type").HasMaxLength(40);
        productEvent.Property(e => e.IdempotencyKey).HasColumnName("idempotency_key").HasMaxLength(160);
        productEvent.Property(e => e.SubjectKey).HasColumnName("subject_key").HasMaxLength(100);
        productEvent.Property(e => e.SessionKey).HasColumnName("session_key").HasMaxLength(100);
        productEvent.Property(e => e.AnswerKey).HasColumnName("answer_key").HasMaxLength(100);
        productEvent.Property(e => e.Rating).HasColumnName("rating");
        productEvent.Property(e => e.SuggestionIndex).HasColumnName("suggestion_index");
        productEvent.Property(e => e.SuggestionCount).HasColumnName("suggestion_count");
        productEvent.Property(e => e.IsFallback).HasColumnName("is_fallback");
        productEvent.Property(e => e.Grounded).HasColumnName("grounded");
        productEvent.Property(e => e.SourceCount).HasColumnName("source_count");
        productEvent.Property(e => e.IsExcluded).HasColumnName("is_excluded").HasDefaultValue(false);
        productEvent.Property(e => e.ExclusionReason).HasColumnName("exclusion_reason").HasMaxLength(40);
        productEvent.Property(e => e.OccurredTime).HasColumnName("occurred_time");
    }
}
