using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    public partial class AddSavedEvents : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "saved_events",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    event_id = table.Column<string>(type: "character varying(200)", maxLength: 200, nullable: false),
                    source = table.Column<string>(type: "text", nullable: false),
                    source_id = table.Column<string>(type: "text", nullable: false),
                    kind = table.Column<string>(type: "text", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    start_date = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    end_date = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    url = table.Column<string>(type: "text", nullable: true),
                    remind_enabled = table.Column<bool>(type: "boolean", nullable: false, defaultValue: true),
                    remind_days_before = table.Column<string>(type: "text", nullable: false, defaultValue: "7,1"),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_saved_events", x => x.id);
                    table.ForeignKey(
                        name: "FK_saved_events_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_saved_events_user_id_event_id",
                table: "saved_events",
                columns: new[] { "user_id", "event_id" },
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "saved_events");
        }
    }
}
