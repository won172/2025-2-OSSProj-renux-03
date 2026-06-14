namespace RenuxServer.Dtos.ChatDtos;

public class ChatMessageDto
{
    public Guid Id { get; init; }
    public Guid ChatId { get; init; }

    public bool IsAsk { get; init; }

    public string Content { get; init; } = null!;

    public DateTime CreatedTime { get; init; }

    public List<ChatSourceDto>? Sources { get; init; }

    public string? RequestId { get; init; }

    public bool IsFallback { get; init; }

    public string? FallbackReason { get; init; }
}

public class ChatSourceDto
{
    public string? Source { get; init; }

    public string? ChunkId { get; init; }

    public string? Title { get; init; }

    public string? Url { get; init; }

    public string? PublishedAt { get; init; }

    public string? Snippet { get; init; }

    public double? VectorScore { get; init; }

    public double? SparseScore { get; init; }

    public double? HybridScore { get; init; }

    public double? RecencyScore { get; init; }

    public double? FinalScore { get; init; }
}
