using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

/// <summary>
/// Privacy-minimal product analytics event. Deliberately uses explicit columns
/// instead of an open JSON payload so questions, answers and direct identifiers
/// cannot accidentally enter the analytics store.
/// </summary>
public class ProductEvent
{
    [Key]
    public Guid Id { get; init; }

    [Required]
    [MaxLength(40)]
    public string EventType { get; init; } = null!;

    [Required]
    [MaxLength(160)]
    public string IdempotencyKey { get; init; } = null!;

    [MaxLength(100)]
    public string? SubjectKey { get; init; }

    [MaxLength(100)]
    public string? SessionKey { get; init; }

    [MaxLength(100)]
    public string? AnswerKey { get; init; }

    public int? Rating { get; init; }

    public int? SuggestionIndex { get; init; }

    // 추천질문은 답변 완료 후 별도 요청으로 생성되므로 완료 이벤트의 최종 개수만
    // 사후 갱신할 수 있다. 그 외 식별·품질 필드는 계속 init-only로 유지한다.
    public int? SuggestionCount { get; set; }

    public bool? IsFallback { get; init; }

    public bool? Grounded { get; init; }

    public int? SourceCount { get; init; }

    [Required]
    public bool IsExcluded { get; init; }

    [MaxLength(40)]
    public string? ExclusionReason { get; init; }

    [Required]
    public DateTime OccurredTime { get; init; } = DateTime.UtcNow;
}
