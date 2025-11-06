using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class Major
{
    [Key]
    public Guid Id { get; init; } 
    [Required]
    public string Majorname { get; set; } = null!;
}
