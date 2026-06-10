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
    public DateTime CreatedTime { get; init; } = DateTime.UtcNow;

    public string? SourcesJson { get; set; }

    [Required]
    public bool IsFallback { get; set; } = false;

    public string? FallbackReason { get; set; }
}
