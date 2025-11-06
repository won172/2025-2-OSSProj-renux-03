using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    public partial class DepartmentToMajor : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.RenameColumn(
                name: "department_name",
                table: "majors",
                newName: "major_name");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.RenameColumn(
                name: "major_name",
                table: "majors",
                newName: "department_name");
        }
    }
}
