using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class Organization
{
    [Key]
    public Guid Id { get; init; } 
    
    public Major? Major { get; set; }         // 외래키
    [Required]
    public Guid MajorId { get; set; }

    [Required]
    public bool IsActive { get; set; } = false;
    [Required]
    public long CreatedTime { get; init; } = DateTime.Now.Ticks;
    [Required]
    public long UpdatedTime { get; set; } = DateTime.Now.Ticks;
}
