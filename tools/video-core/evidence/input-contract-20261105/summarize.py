import json,sys
d=json.load(open(sys.argv[1]))
print(d['source_head'][:12],'dirty=',d['working_tree_dirty'],d['python'])
KEEP=('exit','traceback_on_stderr','stdout_json','ok','result','codes','exception','hold_codes','media_process_launched','files_created','equals_pinned_baseline','bytes_equal_to_library_export','key_emitted_as_literal_utf8','root_in_stdout_as_utf8','issue_path_decodes_to_exact_key','error_kind','issue_codes')
for sec in ('F-CLI-1','F-AUD-1','F-REC-1_F-REC-2','F-ENC-1'):
    print('==',sec)
    for k,v in d[sec].items():
        if isinstance(v,dict):
            print(' ',k[:75],{kk:vv for kk,vv in v.items() if kk in KEEP})
        else: print(' ',k,v)
