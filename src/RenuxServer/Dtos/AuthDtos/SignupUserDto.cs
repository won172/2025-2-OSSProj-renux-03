using System.Text.Json;
using System.Text.Json.Serialization;

namespace RenuxServer.Dtos.AuthDtos;

public class SignupUserDto
{
    public string UserId { get; init; } = null!;
    public string Password { get; init; } = null!;
    public string Username { get; init; } = null!;

    public Guid MajorId { get; init; }
    // RoleId는 클라이언트가 지정할 수 없다(권한 상승 방지). 서버가 항상 기본 역할(일반학생)을 강제한다.
    [JsonExtensionData]
    public IDictionary<string, JsonElement>? ExtraFields { get; init; }

    public bool HasForbiddenRoleInput()
    {
        if (ExtraFields is null) return false;

        return ExtraFields.Keys.Any(key =>
            string.Equals(key, "roleId", StringComparison.OrdinalIgnoreCase)
            || string.Equals(key, "role", StringComparison.OrdinalIgnoreCase)
            || string.Equals(key, "roleName", StringComparison.OrdinalIgnoreCase));
    }
}
