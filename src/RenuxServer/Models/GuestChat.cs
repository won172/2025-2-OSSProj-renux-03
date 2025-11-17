using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class GuestChat
{
    [Key]
    public Guid Id { get; init; }
    [Required]
    public Guid OrganizationId { get; init; }
    public Organization? Organization { get; init; }
    [Required]
    public string Title { get; set; } = null!;
    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.Now.ToUniversalTime();
    [Required]
    public DateTime UpdatedTime { get; set; } = DateTime.Now.ToUniversalTime();
}
