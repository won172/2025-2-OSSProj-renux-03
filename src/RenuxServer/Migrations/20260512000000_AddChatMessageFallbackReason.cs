using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    [Migration("20260512000000_AddChatMessageFallbackReason")]
    public partial class AddChatMessageFallbackReason : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // 과거 Program.cs의 raw ALTER로 컬럼이 먼저 생긴 DB가 있을 수 있어 IF NOT EXISTS로 멱등 처리
            migrationBuilder.Sql("""
                ALTER TABLE chat_messages
                ADD COLUMN IF NOT EXISTS fallback_reason text;
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "fallback_reason",
                table: "chat_messages");
        }
    }
}
