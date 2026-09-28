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

    // Assistant answers are grouped by the user question they answer. Welcome
    // messages and user questions intentionally leave this null.
    public ChatMessage? ParentQuestion { get; set; }
    public Guid? ParentQuestionId { get; set; }

    // Version/current only carry semantic meaning for answers with a parent.
    // Previous versions remain in the table as an audit trail.
    public int? AnswerVersion { get; set; }
    public DateTime? VersionCreatedTime { get; set; }

    [Required]
    public bool IsCurrent { get; set; } = true;

    public string? SourcesJson { get; set; }

    // Completion metadata is attached only after the RAG stream emitted its
    // explicit terminal event. Partial/cancelled attempts leave these null.
    public string? RequestId { get; set; }

    public string? SuggestedQuestionsJson { get; set; }

    public bool? Grounded { get; set; }

    public double? GroundingScore { get; set; }

    public string? VerificationStatus { get; set; }

    public double? RelevanceScore { get; set; }

    [Required]
    public bool IsFallback { get; set; } = false;

    public string? FallbackReason { get; set; }
}
