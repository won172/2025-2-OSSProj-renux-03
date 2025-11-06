using AutoMapper;
using Microsoft.EntityFrameworkCore;
using RenuxServer.DbContexts;
using RenuxServer.Dtos.EtcDtos;

namespace RenuxServer.Apis;

static public class EtcApis
{
    static public void AddEtcApis(this WebApplication application)
    {
        var app = application.MapGroup("/req");

        app.MapGet("/major", async (ServerDbContext db, IMapper mapper) 
            => Results.Ok(mapper.Map<List<MajorDto>>(await db.Majors.ToListAsync())));

        app.MapGet("/role", async (ServerDbContext db, IMapper mapper) 
            => Results.Ok(mapper.Map<List<RoleDto>>(await db.Roles.ToListAsync())));

        app.MapGet("/orgs", async (ServerDbContext db, IMapper mapper) =>
        {
            List<OrganizationDto> orgs
            = mapper.Map<List<OrganizationDto>>
            (await db.Organizations
            .Include(o => o.Major)
            .Where(o => o.IsActive)
            .ToListAsync());

            return Results.Ok(orgs);
        });
    }
}
