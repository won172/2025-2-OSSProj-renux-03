using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Microsoft.EntityFrameworkCore.Infrastructure;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260613000000_AddCouncilSignupRequests")]
    public partial class AddCouncilSignupRequests : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "council_signup_requests",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<string>(type: "text", nullable: false),
                    password = table.Column<string>(type: "text", nullable: false),
                    user_name = table.Column<string>(type: "text", nullable: false),
                    major_id = table.Column<Guid>(type: "uuid", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false, defaultValue: "pending"),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    reviewed_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    reviewed_by_user_id = table.Column<Guid>(type: "uuid", nullable: true),
                    review_note = table.Column<string>(type: "text", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_council_signup_requests", x => x.id);
                    table.ForeignKey(
                        name: "FK_council_signup_requests_majors_major_id",
                        column: x => x.major_id,
                        principalTable: "majors",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_council_signup_requests_major_id",
                table: "council_signup_requests",
                column: "major_id");

            migrationBuilder.CreateIndex(
                name: "IX_council_signup_requests_status",
                table: "council_signup_requests",
                column: "status");

            migrationBuilder.CreateIndex(
                name: "IX_council_signup_requests_user_id",
                table: "council_signup_requests",
                column: "user_id");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "council_signup_requests");
        }
    }
}
