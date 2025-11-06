using RenuxServer.Models;

namespace RenuxServer.Dtos.AuthDtos;

public class SignupUserDto
{
    public string UserId { get; init; } = null!;
    public string Password { get; init; } = null!;
    public string Username { get; init; } = null!;

    public Guid MajorId { get; init; }
    public Guid RoleId { get; init; }
}
