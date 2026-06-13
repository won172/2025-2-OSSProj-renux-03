using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class CouncilSignupRequest
{
    [Key]
    public Guid Id { get; init; }

    [Required]
    public string UserId { get; init; } = null!;

    [Required]
    public string HashPassword { get; set; } = null!;

    [Required]
    public string Username { get; set; } = null!;

    public Major? Major { get; set; }

    [Required]
    public Guid MajorId { get; set; }

    [Required]
    public string Status { get; set; } = "pending";

    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.UtcNow;

    public DateTime? ReviewedTime { get; set; }

    public Guid? ReviewedByUserId { get; set; }

    public string? ReviewNote { get; set; }
}
