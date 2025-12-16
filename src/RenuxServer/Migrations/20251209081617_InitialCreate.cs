using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

#pragma warning disable CA1814 // Prefer jagged arrays over multidimensional

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    public partial class InitialCreate : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "majors",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    major_name = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_majors", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "roles",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    role_name = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_roles", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "organizations",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    major_id = table.Column<Guid>(type: "uuid", nullable: false),
                    is_active = table.Column<bool>(type: "boolean", nullable: false),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_organizations", x => x.id);
                    table.ForeignKey(
                        name: "FK_organizations_majors_major_id",
                        column: x => x.major_id,
                        principalTable: "majors",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "users",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<string>(type: "text", nullable: false),
                    password = table.Column<string>(type: "text", nullable: false),
                    user_name = table.Column<string>(type: "text", nullable: false),
                    major_id = table.Column<Guid>(type: "uuid", nullable: false),
                    role = table.Column<Guid>(type: "uuid", nullable: false),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_users", x => x.id);
                    table.ForeignKey(
                        name: "FK_users_majors_major_id",
                        column: x => x.major_id,
                        principalTable: "majors",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_users_roles_role",
                        column: x => x.role,
                        principalTable: "roles",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "guest",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    organization_id = table.Column<Guid>(type: "uuid", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_guest", x => x.id);
                    table.ForeignKey(
                        name: "FK_guest_organizations_organization_id",
                        column: x => x.organization_id,
                        principalTable: "organizations",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "active_chats",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    organization_id = table.Column<Guid>(type: "uuid", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_active_chats", x => x.id);
                    table.ForeignKey(
                        name: "FK_active_chats_organizations_organization_id",
                        column: x => x.organization_id,
                        principalTable: "organizations",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_active_chats_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "chat_messages",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    chat_id = table.Column<Guid>(type: "uuid", nullable: false),
                    is_ask = table.Column<bool>(type: "boolean", nullable: false),
                    content = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_chat_messages", x => x.id);
                    table.ForeignKey(
                        name: "FK_chat_messages_active_chats_chat_id",
                        column: x => x.chat_id,
                        principalTable: "active_chats",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.InsertData(
                table: "majors",
                columns: new[] { "id", "major_name" },
                values: new object[,]
                {
                    { new Guid("bc497e11-24ca-4217-bab0-9c9da76eabb8"), "가정교육과" },
                    { new Guid("04109332-bc3c-4f01-9da6-a28444eaa255"), "건설환경공학과" },
                    { new Guid("c45e45c6-dbe4-4298-9da6-4a4da81899e6"), "건축학전공" },
                    { new Guid("a2f11352-d83e-456f-aae1-cf955fd56d07"), "경영정보학과" },
                    { new Guid("cce25f9c-6324-489c-834b-3ff3e23cdac0"), "경영학과" },
                    { new Guid("dca75de0-7418-4487-80a8-87dab2633fb1"), "경제학과" },
                    { new Guid("24a31fde-a97c-46ae-8be7-33bff3f4ab59"), "경찰행정학부" },
                    { new Guid("feabd042-b5d2-452e-87ef-418bcf5b814b"), "관리자" },
                    { new Guid("4e7292af-bc20-473b-8056-8d444a1454d6"), "광고홍보학과" },
                    { new Guid("4777d7df-673c-4a80-8e63-e4568cf5ba34"), "교육학과" },
                    { new Guid("76edd195-a8fa-4f1e-acbd-27cb3ccdf927"), "국어교육과" },
                    { new Guid("088fc6bb-25cd-47a8-bb59-82486dc7b86e"), "국어국문문예창작학부" },
                    { new Guid("5d6c0087-75d7-4aad-b243-dad68a61c647"), "국제통상학전공" },
                    { new Guid("a71d2634-954d-4eea-a3d7-9fb1fb50a411"), "글로벌무역학과" },
                    { new Guid("3558f682-15ad-4d1e-b3a3-4aa0959b500d"), "기계로봇에너지공학과" },
                    { new Guid("a1877a72-144b-4a6a-a6af-590079cd99d7"), "문화유산학과" },
                    { new Guid("51f005b7-ae29-4f65-9a53-2dfaf350ab15"), "물리반도체과학부" },
                    { new Guid("390e91a8-7723-464f-bfce-c7d24251ba1e"), "미디어커뮤니케이션학전공" },
                    { new Guid("087a32f8-9632-4987-8eae-05fb56d026a8"), "미술학부" },
                    { new Guid("129b5901-6e28-4eaf-b2cd-fbab3b6dbaaf"), "바이오환경과학과" },
                    { new Guid("83176026-97e2-4426-9236-05e7fa388810"), "법학과" },
                    { new Guid("0467052e-8f60-4d52-bb63-7e7d7dd03815"), "북한학전공" },
                    { new Guid("51732871-293f-42e5-8d71-8bf6c940f51c"), "불교학부" },
                    { new Guid("e9df2853-737c-468d-8362-b984ab50963c"), "사학과" },
                    { new Guid("d6163fc1-5fb4-4931-914e-b8fcd74fe8b7"), "사회복지상담학과" },
                    { new Guid("327aa7ad-7e37-4095-bee8-6f21a7ee8697"), "사회복지학과" },
                    { new Guid("1505aeee-4204-4038-9bf9-04c36c952dc3"), "사회학전공" },
                    { new Guid("9b522bdc-a097-4766-93c5-5e6b7d52d70c"), "산업시스템공학과" },
                    { new Guid("781cffe6-f93a-4827-b0b2-09f5e62b960a"), "생명과학과" },
                    { new Guid("9eb8c2c9-2d4d-439e-8e66-4115bedf5068"), "수학과" },
                    { new Guid("899ae146-bfed-4a20-8daa-a39285a81b35"), "수학교육과" },
                    { new Guid("b54c8019-26e2-4d92-a06f-1ec0c77a2a69"), "스포츠문화학과" },
                    { new Guid("e28c6856-4bd3-4d41-8ba0-0b348d4c3c51"), "시스템반도체학부" },
                    { new Guid("abcb184c-1e72-4972-b2d1-4098ac3a498c"), "식품산업관리학과" },
                    { new Guid("847b78a2-18cd-4eb7-8492-36c4ce31c814"), "식품생명공학과" },
                    { new Guid("f2dc2982-af0c-480d-954c-c0dbcdf5f08c"), "약학과" },
                    { new Guid("13f2f975-3940-4c89-b191-074c30e8ad80"), "에너지신소재공학과" },
                    { new Guid("441512a2-32bf-4999-b5f3-4a77ce16dbd0"), "역사교육과" },
                    { new Guid("3ebf0da6-d89f-42f7-8f47-b69867115ff9"), "연극학부" },
                    { new Guid("bb550380-a710-4d92-8525-85fd985d8246"), "영어영문학부" },
                    { new Guid("c9273178-0d3a-4e79-a8c8-e669b4958d00"), "영화영상학과" },
                    { new Guid("a0ae587d-199b-467c-a87c-b4711f9fc34f"), "융합보안학과" },
                    { new Guid("18afbbaa-2217-4af8-bcbc-72096e1262f6"), "의료인공지능공학과" },
                    { new Guid("dd6b0ee4-ebde-4d60-b3d7-24c245182b02"), "의생명공학과" },
                    { new Guid("481596ca-a703-40bc-beeb-283f11084f59"), "일본학과" },
                    { new Guid("401500cb-251a-4ab9-869f-900deef03181"), "전자전기공학부" },
                    { new Guid("d79c6c00-330f-49bf-8ac5-a144bec906a4"), "정보통신공학전공" },
                    { new Guid("222cedf0-47eb-43a7-bffd-1dca485ecbc8"), "정치외교학전공" },
                    { new Guid("37e73c77-cad2-409a-a19f-79392f915b92"), "중어중문학과" },
                    { new Guid("55dff211-0731-4dc3-9a00-c073914e3800"), "지능형네트워크융합학과" },
                    { new Guid("2bfa078a-5ff6-4c84-ad7e-e0411c4e302d"), "지리교육과" },
                    { new Guid("9c1316e6-b326-4b03-a36e-d3340adc969b"), "철학과" },
                    { new Guid("cd5e966e-398d-4f05-93c3-a5b97200005c"), "체육교육과" },
                    { new Guid("7ecff9cd-c57c-49c4-bf98-a6fd16dcdfb1"), "총학생회" },
                    { new Guid("3d3674d6-3156-4a99-a0c9-ec0b333ded6e"), "컴퓨터·AI학부" },
                    { new Guid("64847ca2-b35f-4faf-8297-487ce62a33ec"), "통계학과" },
                    { new Guid("31256990-d21d-47ec-919a-d7289008cf6d"), "한국음악과" },
                    { new Guid("5c38a01f-27b5-4948-b01a-616a60bd92e3"), "행정학전공" },
                    { new Guid("df1fe2cb-f813-4b23-826f-43c9e0e775cf"), "화공생물공학과" },
                    { new Guid("d9e307bc-1c44-4d96-a4cd-c30cac1b6f62"), "화학과" },
                    { new Guid("84634248-0faa-45eb-88c7-a8affecdc459"), "회계학과" }
                });

            migrationBuilder.InsertData(
                table: "roles",
                columns: new[] { "id", "role_name" },
                values: new object[,]
                {
                    { new Guid("b4114fd1-c9f0-4171-821f-b53a15faba9b"), "일반학생" },
                    { new Guid("c22bc8f7-98b8-45a3-9053-3b779e027649"), "학생회" },
                    { new Guid("ec62f7d6-069d-4a47-8801-db61b938a299"), "관리자" }
                });

            migrationBuilder.CreateIndex(
                name: "IX_active_chats_organization_id",
                table: "active_chats",
                column: "organization_id");

            migrationBuilder.CreateIndex(
                name: "IX_active_chats_user_id",
                table: "active_chats",
                column: "user_id");

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_chat_id",
                table: "chat_messages",
                column: "chat_id");

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_created_time",
                table: "chat_messages",
                column: "created_time");

            migrationBuilder.CreateIndex(
                name: "IX_guest_organization_id",
                table: "guest",
                column: "organization_id");

            migrationBuilder.CreateIndex(
                name: "IX_majors_major_name",
                table: "majors",
                column: "major_name",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_organizations_major_id",
                table: "organizations",
                column: "major_id",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_roles_role_name",
                table: "roles",
                column: "role_name",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_users_major_id",
                table: "users",
                column: "major_id");

            migrationBuilder.CreateIndex(
                name: "IX_users_role",
                table: "users",
                column: "role");

            migrationBuilder.CreateIndex(
                name: "IX_users_user_id",
                table: "users",
                column: "user_id",
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "chat_messages");

            migrationBuilder.DropTable(
                name: "guest");

            migrationBuilder.DropTable(
                name: "active_chats");

            migrationBuilder.DropTable(
                name: "organizations");

            migrationBuilder.DropTable(
                name: "users");

            migrationBuilder.DropTable(
                name: "majors");

            migrationBuilder.DropTable(
                name: "roles");
        }
    }
}
