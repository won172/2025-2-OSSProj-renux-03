namespace RenuxServer.Dtos.AuthDtos;

public class CouncilSignupRequestDto
{
    public Guid Id { get; init; }
    public string UserId { get; init; } = null!;
    public string Username { get; init; } = null!;
    public Guid MajorId { get; init; }
    public string? MajorName { get; init; }
    public string Status { get; init; } = null!;
    public DateTime CreatedTime { get; init; }
    public DateTime? ReviewedTime { get; init; }
    public string? ReviewNote { get; init; }
}

public class CouncilSignupReviewDto
{
    public string? Note { get; init; }
}
