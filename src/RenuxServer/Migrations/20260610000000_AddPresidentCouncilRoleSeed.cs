using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    [Migration("20260610000000_AddPresidentCouncilRoleSeed")]
    public partial class AddPresidentCouncilRoleSeed : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // 운영 DB에 같은 이름의 역할이 수동 삽입돼 있을 수 있어 idempotent 하게 처리
            migrationBuilder.Sql("""
                INSERT INTO roles (id, role_name)
                VALUES ('7a3f2c44-9d1e-4b6a-8f25-6c0e9b51d7a2', '총학생회')
                ON CONFLICT (role_name) DO NOTHING;
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql("""
                DELETE FROM roles WHERE id = '7a3f2c44-9d1e-4b6a-8f25-6c0e9b51d7a2';
                """);
        }
    }
}
