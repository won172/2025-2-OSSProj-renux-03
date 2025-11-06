using RenuxServer.Models;
using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Dtos.EtcDtos;

public class OrganizationDto
{
    public Guid Id { get; init; }
    public MajorDto Major { get; set; } = null!;
    public bool IsActive { get; set; }
}
