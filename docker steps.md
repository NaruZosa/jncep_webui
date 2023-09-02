Personal notes on building the docker image
1. Edit docker-compose.yml to build locally:
   1. Remove `image: naruzosa/jncep_webui`
   2. Add `build: .` where `image: naruzosa/jncep_webui` was
2. `docker-compose up`
   1. For testing only. Confirm the web UI works and downloads work. 
3. `docker build --tag naruzosa/jncep_webui --tag naruzosa/jncep_webui:v46 .`  (or whatever version jncep is at, and is listed in the requirements as)
4. `docker push naruzosa/jncep_webui:v46`  (or whatever version jncep is at, and is listed in the requirements as)
5. `docker push naruzosa/jncep_webui:latest`