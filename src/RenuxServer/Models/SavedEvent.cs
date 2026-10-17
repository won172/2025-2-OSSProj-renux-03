using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

/// <summary>
/// 사용자가 캘린더에서 저장한 공식 일정과 그 일정의 앱 안 알림 설정.
/// 제목·기간은 저장할 때 RAG 후보에서 가져온 값이며, 알림 동기화 때 같은 후보가 보이면 갱신한다.
/// </summary>
public class SavedEvent
{
    [Key]
    public Guid Id { get; init; } = Guid.NewGuid();

    public User? User { get; set; }
    [Required]
    public Guid UserId { get; set; }

    /// <summary>캘린더 일정 id(RAG 후보 id, 예: schedule:…, notices:…).</summary>
    [Required]
    [MaxLength(200)]
    public string EventId { get; set; } = null!;

    [Required]
    public string Source { get; set; } = null!;

    /// <summary>알림의 SourceId. 같은 일정의 주제별 알림과 알림함에서 한 묶음으로 보이도록 같은 값을 쓴다.</summary>
    [Required]
    public string SourceId { get; set; } = null!;

    [Required]
    public string Kind { get; set; } = null!;

    [Required]
    public string Title { get; set; } = null!;

    public DateTime? StartDate { get; set; }

    [Required]
    public DateTime EndDate { get; set; }

    public string? Url { get; set; }

    [Required]
    public bool RemindEnabled { get; set; } = true;

    [Required]
    public string RemindDaysBefore { get; set; } = "7,1";

    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.UtcNow;

    [Required]
    public DateTime UpdatedTime { get; set; } = DateTime.UtcNow;
}
