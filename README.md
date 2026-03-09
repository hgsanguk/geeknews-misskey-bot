# geeknews-misskey-bot

[GeekNews](https://news.hada.io/)의 RSS 피드에 새 글이 올라오면 Misskey에 투고합니다.
* RSS source: https://news.hada.io/rss/news

# 시작하기

1. 저장소 Clone 후 저장소 경로에서...
2. 초기화: `make init`
3. `.env.example`을 복사 한 후 `.env`로 이름 변경 및 환경에 맞게 입력
4. 봇 시작: `make up`
5. 로그 체크: `make logs`

# 기타 명령어
* 봇 종료: `make down`
* 봇 재시작: `make restart`
* 봇 다시 빌드: `make rebuild`
* DB 확인: `make db`

# Notes
첫 실행 시 이미 피드에 존재하는 게시물은 Misskey에 게시되지 않습니다.

SQLite DB path is ./data/state.db.