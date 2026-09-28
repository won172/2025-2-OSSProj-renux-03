using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260928000000_AddChatVerificationStatusAndRelevanceScore")]
    public partial class AddChatVerificationStatusAndRelevanceScore : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "verification_status",
                table: "chat_messages",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<double>(
                name: "relevance_score",
                table: "chat_messages",
                type: "double precision",
                nullable: true);
        }

        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(name: "verification_status", table: "chat_messages");
            migrationBuilder.DropColumn(name: "relevance_score", table: "chat_messages");
        }
    }
}
