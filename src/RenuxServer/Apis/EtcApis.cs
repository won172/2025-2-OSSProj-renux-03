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

            // 매니저는 학생회 계열 역할만 인정하고(일반 학생이 매니저로 표시되던 버그 수정),
            // 학과별 일괄 조회로 N+1 쿼리를 제거한다.
            string[] managerRoles = ["학생회", "총학생회", "관리자"];
            var managersByMajor = await db.Users
                .Where(u => u.Role != null && managerRoles.Contains(u.Role!.Rolename))
                .GroupBy(u => u.MajorId)
                .Select(g => new { MajorId = g.Key, Name = g.Select(u => u.Username).FirstOrDefault() })
                .ToDictionaryAsync(x => x.MajorId, x => x.Name);

            foreach (var orgDto in orgDtos)
            {
                orgDto.ManagerName = managersByMajor.GetValueOrDefault(orgDto.Major.Id) ?? "-";
            }

            return Results.Ok(orgDtos);
        });
    }
}
