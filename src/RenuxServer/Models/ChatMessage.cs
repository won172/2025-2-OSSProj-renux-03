using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class ChatMessage
{
    [Key]
    public Guid Id { get; init; }

    public ActiveChat? Chat { get; set; }     // 외래키
    [Required]
    public Guid ChatId { get; init; }
    
    [Required]
    public bool IsAsk { get; set; } = true;
    [Required]
    public string Content { get; set; } = null!;
    [Required]
    public long CreatedTime { get; init; } = DateTime.Now.Ticks;
}