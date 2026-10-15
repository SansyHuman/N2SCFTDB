/* Benchmark control: interpreter executable linked to the same liblie.so.
   Explicit arguments select the usual initfile and help-data directory. */
int lie_cli_main(int argc, char **argv);
int main(int argc, char **argv) { return lie_cli_main(argc, argv); }
