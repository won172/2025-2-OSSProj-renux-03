using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class User
{
    [Key]
    public Guid Id { get; init; }
    [Required]
    public string UserId { get; init; } = null!;
    [Required]
    public string HashPassword { get; set; } = null!;
    [Required]
    public string Username { get; set; } = null!;

    public Major? Major { get; set; }           // 외래키
    [Required]
    public Guid MajorId { get; set; }

    public Role? Role { get; set; }             // 외래키
    [Required]
    public Guid RoleId { get; set; }

    [Required]
    public long CreatedTime { get; init; } = DateTime.Now.Ticks;
    [Required]
    public long UpdatedTime { get; set; }
}
