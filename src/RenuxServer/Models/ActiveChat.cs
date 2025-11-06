using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class ActiveChat
{
    [Key]
    public Guid Id { get; init; }
    
    public User? User { get; set; }              // 외래키
    [Required]
    public Guid? UserId { get; init; }
    
    public Organization? Organization { get; set; } // 외래키
    [Required]
    public Guid OrganizationId { get; init; }

    [Required]
    public string Title { get; set; } = null!;
    [Required]
    public long CreatedTime { get; init; } = DateTime.Now.Ticks;
    [Required]
    public long UpdatedTime { get; set; } = DateTime.Now.Ticks;
}
