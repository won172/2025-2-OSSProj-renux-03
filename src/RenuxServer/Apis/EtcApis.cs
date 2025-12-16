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
            var organizations = await db.Organizations
                .Include(o => o.Major)
                .Where(o => o.IsActive)
                .ToListAsync();

            List<OrganizationDto> orgDtos = mapper.Map<List<OrganizationDto>>(organizations);

            foreach (var orgDto in orgDtos)
            {
                // Find a user associated with this major (assuming they are the manager/council)
                // In a real scenario, you might filter by Role as well.
                var manager = await db.Users
                    .Where(u => u.MajorId == orgDto.Major.Id)
                    .Select(u => u.Username)
                    .FirstOrDefaultAsync();
                
                orgDto.ManagerName = manager ?? "-";
            }

            return Results.Ok(orgDtos);
        });
    }
}
