using RenuxServer.Dtos.EtcDtos;
using RenuxServer.Models;

namespace RenuxServer.Dtos.ChatDtos;

public class ActiveChatDto
{
    public Guid Id { get; init; }

    public OrganizationDto Organization { get; init; } = null!;

    public string Title { get; set; } = null!;
}
