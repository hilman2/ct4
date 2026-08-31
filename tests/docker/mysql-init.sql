-- weewx' tests create and drop databases of their own, so the user
-- they connect as needs rights on everything, not just on one schema.
-- testgen.conf names the credentials and does not read them from the
-- environment: host = localhost, user = weewx1, password = weewx1.
-- Two users, because two test files bring their own credentials:
-- testgen.conf uses weewx1, test_manager.py uses weewx. Both are
-- hardcoded and read nothing from the environment.
--
-- Granted for '%' and for 'localhost'. The client reaches the server
-- over the loopback of a shared network namespace and arrives as ::1,
-- which MariaDB matches against '%' but not against 'localhost'.
CREATE USER IF NOT EXISTS 'weewx1'@'%' IDENTIFIED BY 'weewx1';
GRANT ALL PRIVILEGES ON *.* TO 'weewx1'@'%' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'weewx1'@'localhost' IDENTIFIED BY 'weewx1';
GRANT ALL PRIVILEGES ON *.* TO 'weewx1'@'localhost' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'weewx'@'%' IDENTIFIED BY 'weewx';
GRANT ALL PRIVILEGES ON *.* TO 'weewx'@'%' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'weewx'@'localhost' IDENTIFIED BY 'weewx';
GRANT ALL PRIVILEGES ON *.* TO 'weewx'@'localhost' WITH GRANT OPTION;
FLUSH PRIVILEGES;
