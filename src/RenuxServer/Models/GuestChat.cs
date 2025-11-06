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
    public long CreatedTime { get; init; }
    [Required]
    public long UpdatedTime { get; set; }
}
