import json,sys
d=json.load(open(sys.argv[1]))
print(d['source_head'][:12],'dirty=',d['production_or_tests_dirty'],d['python'])
K=('exit','traceback_on_stderr','ffprobe_started','output_entries','issue_codes','status','original_status','result','codes','exception','fake_ffprobe_invocations','run_directory_entries','media_and_receipt_unchanged')
for sec in ('A_public_cli','B_bad_receipts_past_parsing_with_owned_fake_ffprobe'):
    print('==',sec)
    for k,v in d[sec].items(): print(' ',k[:62].ljust(62),{x:v[x] for x in K if x in v})
