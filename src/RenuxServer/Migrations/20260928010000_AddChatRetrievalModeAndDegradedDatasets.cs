using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260928010000_AddChatRetrievalModeAndDegradedDatasets")]
    public partial class AddChatRetrievalModeAndDegradedDatasets : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "retrieval_mode",
                table: "chat_messages",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "degraded_datasets_json",
                table: "chat_messages",
                type: "text",
                nullable: true);
        }

        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(name: "retrieval_mode", table: "chat_messages");
            migrationBuilder.DropColumn(name: "degraded_datasets_json", table: "chat_messages");
        }
    }
}
