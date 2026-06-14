namespace RenuxServer.Dtos.AuthDtos;

public class AdminUserDto
{
    public Guid Id { get; init; }
    public string UserId { get; init; } = null!;
    public string Username { get; init; } = null!;
    public Guid MajorId { get; init; }
    public string? MajorName { get; init; }
    public Guid RoleId { get; init; }
    public string? RoleName { get; init; }
    public DateTime CreatedTime { get; init; }
    public DateTime UpdatedTime { get; init; }
}

public class AdminRoleDto
{
    public Guid Id { get; init; }
    public string RoleName { get; init; } = null!;
}

public class AdminUserUpdateDto
{
    public string? Username { get; init; }
    public Guid? MajorId { get; init; }
    public Guid? RoleId { get; init; }
}

public class AdminUserPasswordResetDto
{
    public string Password { get; init; } = null!;
}
