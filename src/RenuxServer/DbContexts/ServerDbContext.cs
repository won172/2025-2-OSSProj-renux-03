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

        users.ToTable("users").HasIndex(p => p.UserId).IsUnique();
        activeChats.ToTable("active_chats");
        message.ToTable("chat_messages").HasIndex(c => c.CreatedTime);
        org.ToTable("organizations");
        majors.ToTable("majors");
        role.ToTable("roles");
        guest.ToTable("guest");

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
    }
}
