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

        // /role 공개 엔드포인트는 제거되었다. 역할 Guid 노출은 권한 상승 시도에 악용될 수 있고,
        // 회원가입은 더 이상 클라이언트가 역할을 선택하지 않는다(서버가 일반학생으로 강제).

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
